"""Run the voice pipeline (always-on wake word + STT + TTS)."""

import logging
import os
import sys
from string import Template

import yaml

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
from voice.pipeline import VoicePipeline


def load_config(path: str) -> dict:
    with open(path) as f:
        raw = f.read()
    expanded = Template(raw).safe_substitute(os.environ)
    return yaml.safe_load(expanded)


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    config_path = os.environ.get(
        "JARVIS_VOICE_CONFIG", "config/voice.yaml"
    )
    if not os.path.exists(config_path):
        print(f"ERROR: voice config not found at {config_path}")
        sys.exit(1)

    config = load_config(config_path)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    memory = MemoryStore(
        data_dir=os.environ.get("JARVIS_DATA_DIR", "./data")
    )
    tools = ToolRegistry()
    brain = JarvisBrain(api_key=api_key, memory=memory, tools=tools)

    pipeline = VoicePipeline(brain, config)

    try:
        pipeline.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        pipeline.stop()


if __name__ == "__main__":
    main()
