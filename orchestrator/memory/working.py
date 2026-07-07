"""Working memory: rolling buffer of recent turns."""

from collections import deque


class WorkingMemory:
    """Rolling buffer of recent turns. Fast, ephemeral."""

    def __init__(self, max_turns: int = 20):
        self.buffer: deque = deque(maxlen=max_turns)

    def add(self, user_input: str, response: str):
        self.buffer.append({"user": user_input, "jarvis": response})

    def get_recent(self, turns: int = 4) -> str:
        recent = list(self.buffer)[-turns:]
        return "\n".join(
            f"User: {t['user']}\nJARVIS: {t['jarvis']}" for t in recent
        )

    def clear(self):
        self.buffer.clear()
