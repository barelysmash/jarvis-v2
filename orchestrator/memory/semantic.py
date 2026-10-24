"""Semantic memory: vector DB for facts, preferences, learned info."""

import uuid
from datetime import datetime
from typing import Any, Optional, cast

import chromadb
from chromadb.config import Settings


class SemanticMemory:
    """Vector DB for facts, preferences, and learned knowledge."""

    def __init__(self, persist_dir: str = "./data/chroma") -> None:
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name="jarvis_facts",
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, text: str, metadata: Optional[dict[str, Any]] = None) -> None:
        """Store a fact. Embeddings computed automatically by Chroma."""
        now = datetime.now().isoformat()
        meta = (metadata or {}) | {
            "created_at": now,
            "last_accessed": now,
            "access_count": 0,
        }
        self.collection.add(
            documents=[text],
            metadatas=[meta],
            ids=[str(uuid.uuid4())],
        )

    def search(
        self,
        query: str,
        k: int = 5,
        category: Optional[str] = None,
    ) -> list[str]:
        """Semantic search — returns most relevant facts."""
        where = cast(Any, {"category": category}) if category else None
        results = self.collection.query(
            query_texts=[query],
            n_results=k,
            where=where,
        )

        # Track access for retrieved facts
        ids = results["ids"]
        if ids and ids[0]:
            self._record_access(list(ids[0]))

        documents = results["documents"]
        if documents and documents[0]:
            return list(documents[0])
        return []

    def _record_access(self, ids: list[str]) -> None:
        """Update access count and last_accessed for retrieved facts."""
        now = datetime.now().isoformat()
        for fact_id in ids:
            try:
                existing = self.collection.get(ids=[fact_id])
                metadatas = existing["metadatas"]
                if not metadatas:
                    continue
                raw = metadatas[0] or {}
                meta: dict[str, Any] = dict(raw)
                current = meta.get("access_count", 0)
                try:
                    count = int(current) if current is not None else 0
                except (TypeError, ValueError):
                    count = 0
                meta["access_count"] = count + 1
                meta["last_accessed"] = now
                self.collection.update(ids=[fact_id], metadatas=[meta])
            except Exception:
                continue

    def forget(self, query: str, threshold: float = 0.95) -> None:
        """Remove facts matching a query above similarity threshold."""
        results = self.collection.query(query_texts=[query], n_results=10)
        ids = results["ids"]
        distances = results["distances"]
        if not ids or not ids[0] or not distances or not distances[0]:
            return
        for doc_id, dist in zip(ids[0], distances[0]):
            if (1 - dist) >= threshold:
                self.collection.delete(ids=[doc_id])
