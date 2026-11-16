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

    def search_scored(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Like search(), but each hit carries its similarity and category.

        Similarity is 1 minus the cosine distance: 1.0 is the same text,
        lower is looser. Callers that act on a match (deduplicating, showing
        the user what is stored) need it; search() callers do not.
        """
        results = self.collection.query(query_texts=[query], n_results=k)
        ids = results["ids"]
        documents = results["documents"]
        if not ids or not ids[0] or not documents or not documents[0]:
            return []
        distances = results["distances"]
        metadatas = results["metadatas"]
        self._record_access(list(ids[0]))
        hits: list[dict[str, Any]] = []
        for index, text in enumerate(documents[0]):
            distance = distances[0][index] if distances and distances[0] else 1.0
            meta = (metadatas[0][index] if metadatas and metadatas[0] else None) or {}
            hits.append(
                {
                    "text": text,
                    "similarity": round(1 - float(distance), 3),
                    "category": meta.get("category"),
                    "created_at": meta.get("created_at"),
                }
            )
        return hits

    def delete_exact(self, text: str) -> int:
        """Delete every stored fact whose text is exactly ``text``.

        Exact match only, on purpose: forget() deletes by similarity and can
        take neighbours with it. Returns how many were deleted.
        """
        found = self.collection.get(where_document=cast(Any, {"$contains": text}))
        ids = [
            fact_id
            for fact_id, document in zip(
                found["ids"], found["documents"] or [], strict=False
            )
            if document == text
        ]
        if ids:
            self.collection.delete(ids=ids)
        return len(ids)

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
