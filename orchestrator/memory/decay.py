"""Memory decay: forgets low-value facts."""

from datetime import datetime, timedelta
from typing import Optional


class MemoryDecay:
    """Removes low-value facts from semantic memory."""

    def __init__(self, semantic_memory):
        self.semantic = semantic_memory

    def run(
        self,
        max_age_days: int = 90,
        min_access_count: int = 1,
        protected_categories: Optional[set[str]] = None,
    ) -> dict:
        """Forget facts that are old AND rarely accessed.

        Protected categories (e.g., 'preference', 'identity') are kept.
        """
        protected = protected_categories or {
            "preference",
            "identity",
            "critical",
        }
        cutoff = datetime.now() - timedelta(days=max_age_days)

        all_facts = self.semantic.collection.get()
        to_forget = []

        for fact_id, meta in zip(all_facts["ids"], all_facts["metadatas"]):
            if not meta:
                continue

            if meta.get("category") in protected:
                continue

            last_accessed_str = meta.get(
                "last_accessed",
                meta.get("created_at", datetime.now().isoformat()),
            )
            try:
                last_accessed = datetime.fromisoformat(last_accessed_str)
            except (ValueError, TypeError):
                continue

            access_count = meta.get("access_count", 0)

            if last_accessed < cutoff and access_count < min_access_count:
                to_forget.append(fact_id)

        if to_forget:
            self.semantic.collection.delete(ids=to_forget)

        return {
            "forgotten": len(to_forget),
            "retained": len(all_facts["ids"]) - len(to_forget),
        }
