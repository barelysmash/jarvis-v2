"""Main morning briefing orchestrator."""

import asyncio
from datetime import datetime
from typing import Optional

from ..base import Workflow
from .analyzer import BriefingAnalyzer
from .collectors import BriefingCollectors
from .composer import BriefingComposer


class MorningBriefingWorkflow(Workflow):
    """Generates and delivers a morning briefing."""

    @property
    def name(self) -> str:
        return "morning_briefing"

    def run(
        self,
        style: str = "standard",
        location: Optional[str] = None,
        speak: bool = True,
    ) -> dict:
        start = datetime.now()
        self.logger.info("Starting morning briefing (%s)", style)

        if not location:
            home = self.memory.semantic.search(
                "user's home location", k=1
            )
            location = home[0] if home else "auto"

        try:
            # 1. Collect everything in parallel
            collectors = BriefingCollectors(self.tools, self.memory)
            collected = asyncio.run(collectors.collect_all(location))

            # 2. Analyze
            analyzer = BriefingAnalyzer(self.memory, self.brain.client)
            analysis = analyzer.analyze(collected)

            # 3. Pre-briefing actions (the proactive bit)
            preparations = self._proactive_preparations(
                collected, analysis
            )

            # 4. Compose
            user_name_results = self.memory.semantic.search(
                "user's preferred address", k=1
            )
            composer = BriefingComposer(
                self.brain.client,
                user_name=(
                    user_name_results[0]
                    if user_name_results
                    else "Sir"
                ),
            )
            briefing_text = composer.compose(
                collected, analysis, style
            )

            if preparations:
                briefing_text += f"\n\n{preparations}"

            # 5. Deliver
            if speak and "speak" in self.tools._tools:
                self.tools.execute("speak", {"text": briefing_text})

            duration = (datetime.now() - start).total_seconds()

            result = {
                "status": "success",
                "output": briefing_text,
                "duration_seconds": duration,
                "data_sources": [
                    k for k, v in collected.items() if v
                ],
                "callbacks_surfaced": len(
                    analysis.get("callbacks", [])
                ),
                "conflicts_flagged": len(analysis.get("conflicts", [])),
            }

            self.log_execution(result)
            return result

        except Exception as exc:
            self.logger.exception("Briefing failed")
            return {"status": "error", "error": str(exc)}

    def _proactive_preparations(
        self, collected: dict, analysis: dict
    ) -> str:
        """Take small helpful actions before the user asks."""
        actions = []

        coffee_pref = self.memory.semantic.search(
            "morning coffee preference", k=1
        )
        if coffee_pref and "coffee_maker" in self.tools._tools:
            self.tools.execute("coffee_maker", {"action": "start"})
            actions.append("Coffee's brewing.")

        hour = datetime.now().hour
        if hour < 7 and "control_lights" in self.tools._tools:
            self.tools.execute(
                "control_lights",
                {
                    "room": "kitchen",
                    "state": "on",
                    "brightness": 40,
                },
            )
            actions.append("Kitchen lights up.")

        cal = collected.get("calendar")
        if isinstance(cal, list) and cal:
            first = cal[0]
            if isinstance(first, dict) and first.get("attachments"):
                actions.append(
                    f"I've pulled up the docs for your {first.get('title', 'meeting')}."
                )

        return " ".join(actions) if actions else ""
