"""Unified memory interface across all memory types."""

import logging
import os
import re
from datetime import datetime
from typing import Optional

from .working import WorkingMemory
from .semantic import SemanticMemory
from .episodic import EpisodicMemory
from .procedural import ProceduralMemory


logger = logging.getLogger("jarvis.memory")

# A new fact at least this similar to a stored one is the same fact reworded.
DUPLICATE_SIMILARITY = 0.95

_ARTICLES = re.compile(r"\b(?:the|a|an)\b")
_NOT_WORD = re.compile(r"[^a-z0-9 ]+")


def normalize_fact(text: str) -> str:
    """Fold case, punctuation and articles, so trivial rewordings compare equal.

    "User's test fact is purple" and "The user's test fact is purple." both
    become "users test fact is purple".
    """
    folded = _NOT_WORD.sub("", text.lower().replace("-", " "))
    return " ".join(_ARTICLES.sub(" ", folded).split())


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

    def find_duplicate(self, fact: str) -> Optional[str]:
        """The stored fact this one repeats, or None.

        A duplicate is a stored fact that is the same text after
        normalize_fact(), or at least DUPLICATE_SIMILARITY similar.
        """
        wanted = normalize_fact(fact)
        for hit in self.semantic.search_scored(fact, k=3, record_access=False):
            if (
                hit["similarity"] >= DUPLICATE_SIMILARITY
                or normalize_fact(str(hit["text"])) == wanted
            ):
                return str(hit["text"])
        return None

    def remember_fact(self, fact: str, category: str = "general") -> Optional[str]:
        """Store a durable fact unless it repeats one already stored.

        Returns the existing fact when it was a duplicate, else None. Every
        writer goes through here, so the brain's automatic extraction cannot
        re-add a fact the user just asked to have remembered.
        """
        try:
            existing = self.find_duplicate(fact)
        except Exception:
            # A failed lookup must not cost the fact: store it, duplicate or not.
            logger.warning("Duplicate check failed; storing anyway", exc_info=True)
            existing = None
        if existing is not None:
            return existing
        self.semantic.add(fact, metadata={"category": category})
        return None

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
