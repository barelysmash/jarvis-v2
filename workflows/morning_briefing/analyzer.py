"""Briefing analyzer: finds anomalies, conflicts, and callbacks."""


class BriefingAnalyzer:
    """Identifies anomalies, conflicts, and callbacks worth surfacing."""

    def __init__(self, memory, brain_client):
        self.memory = memory
        self.client = brain_client

    def analyze(self, collected: dict) -> dict:
        return {
            "conflicts": self._find_conflicts(collected.get("calendar")),
            "callbacks": self._find_callbacks(collected),
            "anomalies": self._find_anomalies(collected),
            "priorities": self._rank_priorities(collected),
        }

    def _find_conflicts(self, calendar) -> list:
        """Cross-reference calendar with past mentions in conversation."""
        if not calendar or not isinstance(calendar, list):
            return []

        conflicts = []
        for event in calendar:
            if not isinstance(event, dict):
                continue
            related = self.memory.episodic.search(
                event.get("title", ""), k=3
            )
            for r in related:
                if any(
                    word in r["summary"].lower()
                    for word in [
                        "moved",
                        "rescheduled",
                        "canceled",
                        "conflict",
                    ]
                ):
                    conflicts.append(
                        {
                            "event": event["title"],
                            "context": r["summary"],
                            "date_mentioned": r["date"],
                        }
                    )
                    break
        return conflicts

    def _find_callbacks(self, collected: dict) -> list:
        """Things the user previously asked JARVIS to follow up on."""
        callbacks = []
        tracking_facts = self.memory.semantic.search(
            "user wants to be informed about", k=10, category="tracking"
        )

        for fact in tracking_facts:
            for category, data in collected.items():
                if data and self._is_relevant(fact, str(data)):
                    callbacks.append(
                        {"tracked": fact, "update_in": category}
                    )
        return callbacks

    def _is_relevant(self, fact: str, data_str: str) -> bool:
        """Quick keyword overlap heuristic."""
        fact_keywords = set(fact.lower().split()) - {
            "the",
            "a",
            "an",
            "user",
            "wants",
            "to",
            "be",
            "informed",
            "about",
        }
        data_lower = data_str.lower()
        return sum(1 for kw in fact_keywords if kw in data_lower) >= 2

    def _find_anomalies(self, collected: dict) -> list:
        """Things outside the user's normal patterns."""
        anomalies = []

        weather = collected.get("weather")
        if isinstance(weather, dict) and weather.get("temp_f"):
            temp = weather["temp_f"]
            if temp < 35 or temp > 90:
                anomalies.append(f"Unusual temperature: {temp}F")

        traffic = collected.get("traffic")
        if isinstance(traffic, dict) and traffic.get("delay_minutes", 0) > 15:
            anomalies.append(
                f"Commute is {traffic['delay_minutes']} min slower than typical"
            )

        messages = collected.get("messages")
        if isinstance(messages, dict) and messages.get("count", 0) > 10:
            anomalies.append(
                f"{messages['count']} unread messages - heavier than usual"
            )

        return anomalies

    def _rank_priorities(self, collected: dict) -> list:
        """What deserves the user's attention first today."""
        priorities = []

        cal = collected.get("calendar")
        if isinstance(cal, list) and cal:
            priorities.append(
                {"type": "first_meeting", "what": cal[0], "rank": 1}
            )

        msgs = collected.get("messages")
        if isinstance(msgs, dict) and msgs.get("urgent"):
            priorities.append(
                {
                    "type": "urgent_message",
                    "what": msgs["urgent"][0],
                    "rank": 1,
                }
            )

        return sorted(priorities, key=lambda p: p["rank"])
