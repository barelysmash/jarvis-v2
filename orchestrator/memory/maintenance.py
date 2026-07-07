"""Maintenance orchestrator: runs decay, consolidation, and summarization."""

import logging
from datetime import datetime

from .decay import MemoryDecay
from .consolidation import MemoryConsolidation
from .summarization import MemorySummarization

logger = logging.getLogger("jarvis.memory.maintenance")


class SleepCycle:
    """Runs nightly memory maintenance: decay, consolidate, summarize."""

    def __init__(self, memory_store, anthropic_client):
        self.memory = memory_store
        self.decay = MemoryDecay(memory_store.semantic)
        self.consolidation = MemoryConsolidation(
            memory_store.semantic, anthropic_client
        )
        self.summarization = MemorySummarization(
            memory_store.episodic,
            anthropic_client,
            memory_store.episodic.db_path,
        )

    def run(self, mode: str = "nightly") -> dict:
        """Run maintenance.

        Modes: 'nightly' (decay + summarize), 'weekly' (+ consolidate),
        'full' (all).
        """
        start = datetime.now()
        report = {"started_at": start.isoformat(), "mode": mode}

        try:
            logger.info("Running decay...")
            report["decay"] = self.decay.run()

            logger.info("Running summarization...")
            report["summarization"] = self.summarization.run()

            if mode in {"weekly", "full"}:
                logger.info("Running consolidation...")
                report["consolidation"] = self.consolidation.run()

            report["status"] = "success"
        except Exception as exc:
            logger.exception("Sleep cycle failed")
            report["status"] = "error"
            report["error"] = str(exc)

        report["duration_seconds"] = (datetime.now() - start).total_seconds()
        return report
