"""Memory consolidation: merges semantically similar facts."""

from typing import Optional


class MemoryConsolidation:
    """Merges semantically similar facts into consolidated entries."""

    def __init__(self, semantic_memory, anthropic_client):
        self.semantic = semantic_memory
        self.client = anthropic_client

    def run(self, similarity_threshold: float = 0.85) -> dict:
        """Find clusters of similar facts and merge them."""
        all_facts = self.semantic.collection.get()
        if len(all_facts["ids"]) < 2:
            return {"merged": 0, "clusters": 0}

        clusters = self._find_clusters(all_facts, similarity_threshold)
        merged_count = 0

        for cluster in clusters:
            if len(cluster) < 2:
                continue

            cluster_facts = [
                all_facts["documents"][all_facts["ids"].index(fid)]
                for fid in cluster
            ]
            cluster_metas = [
                all_facts["metadatas"][all_facts["ids"].index(fid)]
                for fid in cluster
            ]

            merged_text = self._merge_facts(cluster_facts)
            if not merged_text:
                continue

            merged_meta = {
                "category": cluster_metas[0].get("category", "general"),
                "created_at": min(
                    m.get("created_at", "") for m in cluster_metas
                ),
                "last_accessed": max(
                    m.get("last_accessed", "") for m in cluster_metas
                ),
                "access_count": sum(
                    m.get("access_count", 0) for m in cluster_metas
                ),
                "consolidated_from": len(cluster),
            }

            self.semantic.collection.delete(ids=cluster)
            self.semantic.add(merged_text, metadata=merged_meta)
            merged_count += len(cluster) - 1

        return {"merged": merged_count, "clusters": len(clusters)}

    def _find_clusters(
        self, all_facts: dict, threshold: float
    ) -> list[list[str]]:
        """Group facts by semantic similarity."""
        clustered: set[str] = set()
        clusters: list[list[str]] = []

        for i, fact_id in enumerate(all_facts["ids"]):
            if fact_id in clustered:
                continue

            doc = all_facts["documents"][i]
            similar = self.semantic.collection.query(
                query_texts=[doc],
                n_results=min(10, len(all_facts["ids"])),
            )

            cluster = [fact_id]
            for sim_id, dist in zip(
                similar["ids"][0], similar["distances"][0]
            ):
                similarity = 1 - dist
                if (
                    sim_id != fact_id
                    and similarity >= threshold
                    and sim_id not in clustered
                ):
                    cluster.append(sim_id)
                    clustered.add(sim_id)

            clustered.add(fact_id)
            if len(cluster) > 1:
                clusters.append(cluster)

        return clusters

    def _merge_facts(self, facts: list[str]) -> Optional[str]:
        """Use Claude to synthesize duplicate facts into one clean version."""
        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system=(
                    "Merge these similar facts about the user into one clean, "
                    "comprehensive statement. Preserve all unique information. "
                    "Return only the merged fact, no preamble."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": "Facts to merge:\n"
                        + "\n".join(f"- {f}" for f in facts),
                    }
                ],
            )
            text = response.content[0].text.strip() if response.content else None
            return text if text else None
        except Exception:
            return None
