"""Unified memory interface across all memory types."""

import os
from datetime import datetime
from typing import Optional

from .working import WorkingMemory
from .semantic import SemanticMemory
from .episodic import EpisodicMemory
from .procedural import ProceduralMemory


class MemoryStore:
    """Unified interface across all memory types."""

    def __init__(self, data_dir: str = "./data"):
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(f"{data_dir}/chroma", exist_ok=True)

        self.data_dir = data_dir
        self.working = WorkingMemory(max_turns=20)
        self.semantic = SemanticMemory(persist_dir=f"{data_dir}/chroma")
        self.episodic = EpisodicMemory(db_path=f"{data_dir}/jarvis.db")
        self.procedural = ProceduralMemory(db_path=f"{data_dir}/jarvis.db")

    def retrieve(self, query: str, k: int = 5) -> str:
        """Pull relevant context for the brain's system prompt."""
        recent = self.working.get_recent(turns=4)
        facts = self.semantic.search(query, k=k)
        episodes = self.episodic.search(query, k=3)
        return self._format_context(recent, facts, episodes)

    def store(
        self,
        user_input: str,
        response: str,
        metadata: Optional[dict] = None,
    ):
        """Persist an interaction across appropriate memory types."""
        timestamp = datetime.now().isoformat()
        self.working.add(user_input, response)
        self.episodic.log(user_input, response, timestamp, metadata)

    def remember_fact(self, fact: str, category: str = "general"):
        """Explicitly store a durable fact in semantic memory."""
        self.semantic.add(fact, metadata={"category": category})

    def _format_context(
        self,
        recent: str,
        facts: list[str],
        episodes: list[dict],
    ) -> str:
        parts = []
        if facts:
            parts.append(
                "Known facts:\n" + "\n".join(f"- {f}" for f in facts)
            )
        if episodes:
            parts.append(
                "Relevant past interactions:\n"
                + "\n".join(
                    f"- [{e['date']}] {e['summary']}" for e in episodes
                )
            )
        if recent:
            parts.append("Recent conversation:\n" + recent)
        return "\n\n".join(parts) if parts else "No prior context."
