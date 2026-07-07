"""Run the memory maintenance sleep cycle. Schedule via cron or systemd timer."""

import json
import os
import sys
from datetime import datetime

import anthropic

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        pass

from orchestrator.memory.maintenance import SleepCycle
from orchestrator.memory.store import MemoryStore


def main():
    load_dotenv()
    mode = sys.argv[1] if len(sys.argv) > 1 else "nightly"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    memory = MemoryStore(
        data_dir=os.environ.get("JARVIS_DATA_DIR", "./data")
    )
    client = anthropic.Anthropic(api_key=api_key)

    cycle = SleepCycle(memory, client)
    report = cycle.run(mode=mode)

    print(json.dumps(report, indent=2))

    # Log report to disk
    log_dir = f"{memory.data_dir}/sleep_logs"
    os.makedirs(log_dir, exist_ok=True)
    log_path = f"{log_dir}/{datetime.now().strftime('%Y%m%d')}.json"
    with open(log_path, "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
