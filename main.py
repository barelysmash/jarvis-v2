"""JARVIS CLI: simple text-based interaction."""

import os
import sys

# Ensure the package root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        # Silent no-op if python-dotenv isn't installed.
        # Env vars can still be set in the shell directly.
        pass

from orchestrator.brain import JarvisBrain
from orchestrator.memory.store import MemoryStore
from orchestrator.tools import ToolRegistry


def main():
    load_dotenv()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Copy .env.template to .env.")
        sys.exit(1)

    memory = MemoryStore(
        data_dir=os.environ.get("JARVIS_DATA_DIR", "./data")
    )
    tools = ToolRegistry()

    # Optionally register calendar
    if os.path.exists("config/google/token.json"):
        try:
            from tools.integrations.calendar import GoogleCalendar

            calendar = GoogleCalendar(
                timezone_name=os.environ.get(
                    "JARVIS_TIMEZONE", "America/Chicago"
                )
            )
            tools.register_calendar(calendar)
            print("[main] Google Calendar integration loaded")
        except Exception as exc:
            print(f"[main] Calendar registration skipped: {exc}")
    # Register BarelySwingTrade (read book + arm/disarm engine; same-host API)
    try:
        from tools.integrations.barelyswing import BarelySwingAdapter
        BarelySwingAdapter().register(tools)
        print("[main] BarelySwingTrade tools loaded")
    except Exception as exc:
        print(f"[main] BarelySwing registration skipped: {exc}")

    # Register web search if API key available
    try:
        if os.environ.get("TAVILY_API_KEY"):
            from tools.integrations.web_search import TavilyAdapter
            TavilyAdapter().register(tools)
            print("[main] Web search (Tavily) loaded")
    except Exception as e:
            print(f"[main] Web search not available: {e}")

    jarvis = JarvisBrain(
        api_key=api_key,
        user_name=os.environ.get("JARVIS_USER_NAME", "Sir"),
        memory=memory,
        tools=tools,
    )

    print("JARVIS online. Type 'exit' to quit.")
    print()

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "shutdown"}:
            print("Powering down. Good evening, Sir.")
            break

        response = jarvis.think_and_act(user_input)
        print(f"\nJARVIS: {response}\n")


if __name__ == "__main__":
    main()
