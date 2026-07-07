"""Base class for multi-step workflows."""

import logging
from abc import ABC, abstractmethod
from datetime import datetime


class Workflow(ABC):
    """Base class for multi-step workflows."""

    def __init__(self, brain, memory, tools):
        self.brain = brain
        self.memory = memory
        self.tools = tools
        self.logger = logging.getLogger(f"jarvis.workflow.{self.name}")

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def run(self, **context) -> dict: ...

    def log_execution(self, result: dict):
        """Persist workflow runs to episodic memory."""
        self.memory.episodic.log(
            user_input=f"[workflow:{self.name}]",
            response=result.get("output", ""),
            timestamp=datetime.now().isoformat(),
            metadata={
                "workflow": self.name,
                "status": result.get("status"),
            },
        )
