"""Trigger the morning briefing. Schedule via systemd timer."""

import argparse
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        pass

from orchestrator.brain import JarvisBrain
from orchestrator.memory.store import MemoryStore
from orchestrator.tools import ToolRegistry
from workflows.morning_briefing.briefing import MorningBriefingWorkflow


def main():
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--style",
        default="standard",
        choices=["quick", "standard", "detailed"],
    )
    parser.add_argument("--no-speak", action="store_true")
    parser.add_argument("--location", default=None)
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    memory = MemoryStore(
        data_dir=os.environ.get("JARVIS_DATA_DIR", "./data")
    )
    tools = ToolRegistry()

    if os.path.exists("config/google/token.json"):
        try:
            from tools.integrations.calendar import GoogleCalendar

            calendar = GoogleCalendar(
                timezone_name=os.environ.get(
                    "JARVIS_TIMEZONE", "America/Chicago"
                )
            )
            tools.register_calendar(calendar)
        except Exception as exc:
            print(f"[briefing] Calendar setup failed: {exc}")

    brain = JarvisBrain(
        api_key=api_key, memory=memory, tools=tools, origin="briefing"
    )

    from orchestrator import event_log

    workflow = MorningBriefingWorkflow(brain, memory, tools)
    with event_log.job("briefing", style=args.style) as rec:
        result = workflow.run(
            style=args.style,
            location=args.location,
            speak=not args.no_speak,
        )
        if result.get("status") != "success":
            raise RuntimeError(f"Briefing failed: {result.get('error')}")
        rec["summary"] = str(result.get("output", ""))[:400]

    print(result["output"])
    print(
        f"\n[completed in {result['duration_seconds']:.1f}s]"
    )


if __name__ == "__main__":
    main()
