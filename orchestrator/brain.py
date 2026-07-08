"""JARVIS brain: the reasoning core with tool use."""

import logging
import re
import json
from typing import Optional

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
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.user_name = user_name
        self.tools = tools or ToolRegistry()
        self.memory = memory or MemoryStore()
        self.conversation: list[dict] = []

    # ─── Public API ──────────────────────────────────────────

    def think_and_act(self, user_input: str, max_iterations: int = 10) -> str:
        """Run the ReAct loop: reason, call tools, observe, repeat until done."""
        context = self.memory.retrieve(user_input, k=5)
        from datetime import datetime
        now_str = datetime.now().strftime("%A, %B %-d, %Y at %-I:%M %p")
        system_prompt = SYSTEM_PROMPT.format(
            user_name=self.user_name,
            memory_context=context,
            current_time=now_str,
        )

        self._repair_dangling_tool_use()
        self._trim_history()
        self.conversation.append({"role": "user", "content": user_input})

        for iteration in range(max_iterations):
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
                return f"My apologies, {self.user_name} - I encountered an issue: {exc}"

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
                        self._emit_tool_event(block.name, block.input, "running")

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
                        self._emit_tool_event(block.name, block.input, status)

                        # If this was a calendar list and it succeeded, push to widget
                        if block.name == "calendar_list_events" and not is_error:
                            self._emit_widget("calendar", result)

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

    def _emit_tool_event(self, name: str, args: dict, status: str):
        """Fire a tool event to both the in-process bus and the SQLite log."""
        # Write to event log (works across processes)
        try:
            from orchestrator import event_log
            event_log.emit(
                source="brain",
                event_type="tool",
                payload={"name": name, "args": args, "status": status},
            )
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
