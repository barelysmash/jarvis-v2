"""Briefing composer: turns collected data into natural narration."""


class BriefingComposer:
    """Assembles collected data + analysis into a natural briefing."""

    def __init__(self, brain_client, user_name: str = "Sir"):
        self.client = brain_client
        self.user_name = user_name

    def compose(
        self,
        collected: dict,
        analysis: dict,
        style: str = "standard",
    ) -> str:
        """Compose the briefing.

        Styles: 'standard' (~30s), 'detailed' (~60s), 'quick' (~15s).
        """
        prompt = self._build_prompt(collected, analysis, style)

        try:
            response = self.client.messages.create(
                model="claude-opus-4-7",
                max_tokens=600,
                system=self._system_prompt(),
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except Exception as exc:
            return f"Briefing composition failed: {exc}"

    def _system_prompt(self) -> str:
        return (
            f"You are JARVIS delivering a morning briefing to "
            f"{self.user_name}.\n\n"
            "Voice: Warm but efficient. Dry wit when appropriate. "
            "Never robotic.\n\n"
            "Structure (in order, but skip any section with no real data):\n"
            "1. Brief greeting + time + weather (one sentence)\n"
            "2. Schedule highlights (only mention events actually in CALENDAR data)\n"
            "3. Conditions affecting the day (traffic, anything unusual)\n"
            "4. Anomalies and callbacks - ONLY if explicitly listed in "
            "ANOMALIES or CALLBACKS sections of the input. NEVER invent these.\n"
            "5. End with a soft handoff ('Anything else?' or similar)\n\n"
            "ABSOLUTE RULES (these override everything else):\n"
            "- NEVER invent facts not present in the input data.\n"
            "- ALWAYS use the day, date, and time from CURRENT TIME exactly "
            "as given. NEVER invent or guess the day or date.\n"
            "- NEVER reference things the user 'mentioned a few weeks back' "
            "unless that exact phrase appears in CALLBACKS.\n"
            "- NEVER claim a pattern across multiple days (e.g., 'third "
            "Monday in a row') unless ANOMALIES explicitly contains that.\n"
            "- NEVER invent past conversations, past requests, or recurring "
            "themes. If the input has no callbacks, simply don't mention any.\n"
            "- A short briefing is far better than a fabricated one.\n\n"
            "Format rules:\n"
            "- Speak as if it will be heard aloud - short sentences, "
            "natural rhythm\n"
            "- Skip categories with nothing notable\n"
            "- Maximum 150 words for standard style, 80 for quick, "
            "250 for detailed\n"
            "- Do not list things in bullet form - speak in flowing "
            "sentences\n"
            f"- Open with 'Good morning, {self.user_name}'"
        )

    def _build_prompt(
        self, collected: dict, analysis: dict, style: str
    ) -> str:
        from datetime import datetime
        now = datetime.now()
        date_str = now.strftime("%A, %B %-d, %Y at %-I:%M %p")
        # On Windows %-d/%-I aren't supported; fall back if needed
        # (this code runs on the briefing target, which is Linux, so we're fine)

        sections = [f"CURRENT TIME: {date_str}"]

        if collected.get("weather"):
            sections.append(f"WEATHER: {collected['weather']}")
        if collected.get("calendar"):
            sections.append(f"CALENDAR: {collected['calendar']}")
        if collected.get("traffic"):
            sections.append(f"TRAFFIC: {collected['traffic']}")
        if collected.get("messages"):
            sections.append(f"MESSAGES: {collected['messages']}")
        if collected.get("news"):
            sections.append(f"NEWS: {collected['news']}")
        if collected.get("tracked"):
            sections.append(f"TRACKED ITEMS: {collected['tracked']}")
        if analysis.get("conflicts"):
            sections.append(
                f"CONFLICTS DETECTED: {analysis['conflicts']}"
            )
        if analysis.get("callbacks"):
            sections.append(
                f"CALLBACKS (mention these): {analysis['callbacks']}"
            )
        if analysis.get("anomalies"):
            sections.append(f"ANOMALIES: {analysis['anomalies']}")

        return f"Style: {style}\n\n" + "\n\n".join(sections)
