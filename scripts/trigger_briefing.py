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

BRIEFING_READY = "Your morning briefing is ready."


def push_briefing_to_phone(result, notifier) -> int:
    """Tell the phone the briefing ran, and push each alert on its own.

    The briefing text itself is not sent: it is long, and it is spoken and
    shown already. Alerts are sent verbatim, as they are in the briefing.
    Returns how many notifications went out.
    """
    if notifier is None:
        return 0
    sent = 0
    if notifier.try_send(BRIEFING_READY, title="Morning briefing", priority="low"):
        sent += 1
    for alert in result.get("alerts") or []:
        if notifier.try_send(str(alert), title="Atlas alert", priority="high"):
            sent += 1
    return sent


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

    # Atlas, for the staleness alarm only. Skipped without a token, exactly
    # as the agents registry skips it in the API server.
    if os.environ.get("ATLAS_API_TOKEN"):
        try:
            from tools.integrations.atlas import AtlasAdapter

            AtlasAdapter().register(tools)
        except Exception as exc:
            print(f"[briefing] Atlas setup failed: {exc}")

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

    from tools.integrations.phone import notifier_from_env

    pushed = push_briefing_to_phone(result, notifier_from_env())
    if pushed:
        print(f"[briefing] {pushed} phone notification(s) sent")

    print(result["output"])
    print(
        f"\n[completed in {result['duration_seconds']:.1f}s]"
    )


if __name__ == "__main__":
    main()
