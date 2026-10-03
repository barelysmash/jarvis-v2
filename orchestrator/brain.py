"""JARVIS brain: the reasoning core with tool use."""

import logging
import re
import json
import time
import uuid
from typing import Any, Optional

import anthropic

from .tools import ToolRegistry
from .memory.store import MemoryStore
from .personality import SYSTEM_PROMPT

logger = logging.getLogger("jarvis.brain")


class JarvisBrain:
    """The reasoning core. Runs a ReAct loop over Claude with tool use."""

    def __init__(
        self,
        api_key: str,
        user_name: str = "Sir",
        model: str = "claude-opus-4-7",
        memory: Optional[MemoryStore] = None,
        tools: Optional[ToolRegistry] = None,
        origin: Optional[str] = None,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        # Who drives this brain (api, briefing, voice...) — cockpit label.
        from orchestrator.event_log import process_label
        self.origin = origin or process_label()
        self._turn_id: Optional[str] = None
        self._turn_stats: dict[str, int] = {}
        self.model = model
        self.user_name = user_name
        self.tools = tools or ToolRegistry()
        self.memory = memory or MemoryStore()
        self.conversation: list[dict] = []

    # ─── Public API ──────────────────────────────────────────

    def think_and_act(
        self,
        user_input: str,
        max_iterations: int = 10,
        runtime_context: Optional[dict] = None,
    ) -> str:
        """Run the ReAct loop: reason, call tools, observe, repeat until done.

        Every turn is bracketed with turn.start / turn.end events (and an
        llm event per model round-trip) so the cockpit can render the full
        exchange, including the steps the HUD chat panel never shows.
        """
        turn_id = uuid.uuid4().hex[:8]
        self._turn_id = turn_id
        self._turn_stats = {"iterations": 0, "tools": 0, "errors": 0,
                            "input_tokens": 0, "output_tokens": 0}
        started = time.time()
        self._log_event("turn.start", {
            "turn_id": turn_id,
            "origin": self.origin,
            "input": user_input,
            "model": self.model,
            "history": len(self.conversation),
            "runtime_context": runtime_context or None,
        })
        reply = ""
        error: Optional[str] = None
        try:
            reply = self._run_turn(user_input, max_iterations, runtime_context)
            return reply
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._log_event("turn.end", {
                "turn_id": turn_id,
                "origin": self.origin,
                "text": reply,
                "ms": int((time.time() - started) * 1000),
                "error": error,
                **self._turn_stats,
            })
            self._turn_id = None

    def _run_turn(
        self,
        user_input: str,
        max_iterations: int,
        runtime_context: Optional[dict],
    ) -> str:
        context = self.memory.retrieve(user_input, k=5)
        from datetime import datetime
        now_str = datetime.now().strftime("%A, %B %-d, %Y at %-I:%M %p")
        system_prompt = SYSTEM_PROMPT.format(
            user_name=self.user_name,
            memory_context=context,
            current_time=now_str,
        )

        if runtime_context:
            muse_review = runtime_context.get("muse_review")
            if isinstance(muse_review, dict):
                project_id = muse_review.get("project_id")
                artifact_id = muse_review.get("artifact_id")
                if isinstance(project_id, str) and isinstance(artifact_id, str):
                    system_prompt += (
                        "\n\n# Ephemeral Runtime Context\n"
                        "Muse review selection for this turn only:\n"
                        f"- project_id: {project_id}\n"
                        f"- artifact_id: {artifact_id}\n"
                        "This selection is referential context only. "
                        "Selection itself is NOT approval, revision, generation, "
                        "or permission to take any Muse action. "
                        "Only approve the selected artifact when the current "
                        "user message explicitly approves it. "
                        "A revision request is project-scoped and should use "
                        "the selected project_id only."
                    )

        self._repair_dangling_tool_use()
        self._trim_history()
        self.conversation.append({"role": "user", "content": user_input})

        for iteration in range(max_iterations):
            call_started = time.time()
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=system_prompt,
                    tools=self.tools.get_schemas(),
                    messages=self.conversation,
                )
            except Exception as exc:
                logger.exception("Brain API call failed")
                self._turn_stats["errors"] += 1
                self._log_event("llm", {
                    "turn_id": self._turn_id,
                    "iteration": iteration + 1,
                    "model": self.model,
                    "ms": int((time.time() - call_started) * 1000),
                    "stop_reason": "api_error",
                    "error": str(exc),
                })
                return f"My apologies, {self.user_name} - I encountered an issue: {exc}"

            self._log_llm(response, iteration + 1, call_started)

            self.conversation.append(
                {"role": "assistant", "content": response.content}
            )

            if response.stop_reason == "end_turn":
                final_text = self._extract_text(response.content)
                self.memory.store(user_input, final_text)
                self._extract_memorable_facts(user_input, final_text)
                return final_text

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if getattr(block, "type", None) == "tool_use":
                        logger.info("Tool call: %s(%s)", block.name, block.input)
                        self._emit_tool_event(
                            block.name, block.input, "running", call_id=getattr(block, "id", None)
                        )
                        tool_started = time.time()

                        try:
                            result, is_error = self.tools.execute(block.name, block.input)
                        except Exception as exc:
                            # A throwing handler must still yield a
                            # tool_result, or the history ends with an
                            # orphaned tool_use and poisons every
                            # subsequent API call.
                            logger.exception("Tool %s raised", block.name)
                            result, is_error = f"Tool crashed: {exc}", True

                        status = "error" if is_error else "success"
                        self._turn_stats["tools"] += 1
                        if is_error:
                            self._turn_stats["errors"] += 1
                        self._emit_tool_event(
                            block.name, block.input, status,
                            call_id=getattr(block, "id", None),
                            ms=int((time.time() - tool_started) * 1000),
                            result=result,
                        )

                        # If this was a calendar list and it succeeded, push to widget
                        if block.name == "calendar_list_events" and not is_error:
                            self._emit_widget("schedule", {"events": result})

                        # Publish browser-safe Muse review state.
                        tool_name = getattr(block, "name", "")
                        if (
                            isinstance(tool_name, str)
                            and tool_name.startswith("muse_")
                            and not is_error
                        ):
                            try:
                                from tools.integrations.muse import (
                                    build_review_widget,
                                )

                                review = build_review_widget(result)
                                if review is not None:
                                    self._emit_widget("muse_review", review)
                            except Exception:
                                logger.exception(
                                    "Muse review widget emission failed"
                                )

                        # Send tool_result back to Claude with the is_error
                        # flag set. With is_error=True Claude will tell the
                        # user the tool failed instead of improvising an
                        # answer from prior context.
                        tool_result_block = {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result),
                        }
                        if is_error:
                            tool_result_block["is_error"] = True
                        tool_results.append(tool_result_block)

                self.conversation.append({"role": "user", "content": tool_results})
                continue

            # Unknown stop reason - bail
            break

        return "I've reached my reasoning limit. Could you clarify the request?"

    def reset_conversation(self):
        """Wipe in-context history. Memory persists separately."""
        self.conversation = []

    # ─── Internals ───────────────────────────────────────────

    def _repair_dangling_tool_use(self):
        """Drop a trailing assistant turn whose tool_use has no tool_result.

        An interrupted tool loop (exception, max_iterations, restart
        mid-turn) leaves the history ending with an assistant message
        containing tool_use blocks and no following tool_result message.
        The API rejects that history on the next call, which poisons
        every subsequent turn. Repair by dropping the orphan.
        """
        if not self.conversation:
            return
        last = self.conversation[-1]
        if last.get("role") != "assistant":
            return
        content = last.get("content")
        blocks = content if isinstance(content, list) else []
        if any(
            (isinstance(b, dict) and b.get("type") == "tool_use")
            or getattr(b, "type", None) == "tool_use"
            for b in blocks
        ):
            self.conversation.pop()

    def _trim_history(self, max_messages: int = 24):
        """Cap history length, never splitting a tool_use/tool_result pair.

        Trims oldest-first. If the cut would make history start with a
        user message carrying tool_result blocks (whose tool_use just
        got trimmed away), advance the cut past it.
        """
        if len(self.conversation) <= max_messages:
            return
        start = len(self.conversation) - max_messages
        first = self.conversation[start]
        content = first.get("content")
        if first.get("role") == "user" and isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            start += 1
        self.conversation = self.conversation[start:]

    def _log_event(self, event_type: str, payload: dict):
        """Cockpit-only event: SQLite log, never the HUD bus."""
        try:
            from orchestrator import event_log
            event_log.emit(source="brain", event_type=event_type, payload=payload)
        except Exception:
            logger.debug("event emit failed", exc_info=True)

    def _log_llm(self, response, iteration: int, started: float):
        usage = getattr(response, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        stats = getattr(self, "_turn_stats", None)
        if stats is not None:
            stats["iterations"] = iteration
            stats["input_tokens"] += in_tok
            stats["output_tokens"] += out_tok
        self._log_event("llm", {
            "turn_id": self._turn_id,
            "iteration": iteration,
            "model": getattr(response, "model", self.model),
            "ms": int((time.time() - started) * 1000),
            "stop_reason": getattr(response, "stop_reason", None),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "text": self._extract_text(getattr(response, "content", []) or []),
            "tool_calls": [
                getattr(b, "name", "?")
                for b in (getattr(response, "content", []) or [])
                if getattr(b, "type", None) == "tool_use"
            ],
        })

    def _emit_tool_event(
        self,
        name: str,
        args: dict,
        status: str,
        call_id: Optional[str] = None,
        ms: Optional[int] = None,
        result=None,
    ):
        """Fire a tool event to both the in-process bus and the SQLite log.

        The HUD reads name/args/status; the cockpit also uses turn_id,
        call_id (pairs running→done), agent, ms and the clipped result.
        """
        try:
            agent = self.tools.owner_of(name)
        except Exception:
            agent = "unknown"
        # Write to event log (works across processes)
        try:
            from orchestrator import event_log
            payload: dict[str, Any] = {
                "name": name, "args": args, "status": status,
                "turn_id": self._turn_id, "call_id": call_id, "agent": agent,
            }
            if ms is not None:
                payload["ms"] = ms
            if status != "running":
                payload["result"] = event_log.clip(result)
            event_log.emit(source="brain", event_type="tool", payload=payload)
        except Exception:
            pass

        # Also fire on the in-process bus (low-latency for same-process clients)
        try:
            import asyncio
            from server.events import bus

            event = {
                "type": "tool",
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "data": {"name": name, "args": args, "status": status},
            }
            for q in list(bus.subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        except Exception:
            pass

    def _emit_widget(self, widget_name: str, data):
        """Push data to a HUD widget panel (best-effort)."""
        try:
            from orchestrator import event_log
            event_log.emit(
                source="brain",
                event_type="widget",
                payload={"widget": widget_name, "data": data},
            )
        except Exception:
            pass
        try:
            import asyncio
            from server.events import bus

            event = {
                "type": "widget",
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "data": {"widget": widget_name, "data": data},
            }
            for q in list(bus.subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        except Exception:
            pass

    def _extract_text(self, content) -> str:
        return "".join(
            getattr(b, "text", "") for b in content if hasattr(b, "text")
        )

    def _extract_memorable_facts(self, user_input: str, response: str):
        """Use a fast model to extract durable facts worth remembering."""
        try:
            extraction = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=(
                    "Extract durable facts about the user worth remembering. "
                    "Return a JSON list of strings, or [] if nothing notable. "
                    "Only include preferences, recurring info, or stable facts. "
                    "Skip one-off requests, weather queries, etc."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"User said: {user_input}\n"
                            f"JARVIS replied: {response}"
                        ),
                    }
                ],
            )
            text = extraction.content[0].text if extraction.content else "[]"
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                facts = json.loads(match.group())
                for fact in facts:
                    self.memory.remember_fact(fact)
        except Exception:
            logger.debug("Fact extraction failed (non-fatal)", exc_info=True)
