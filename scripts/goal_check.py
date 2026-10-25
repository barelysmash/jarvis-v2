"""Run several commands as one goal condition; exit 0 only if every one does.

    python scripts/goal_check.py -- python -m mypy --strict orchestrator \\
        -- python -m pytest -q tests/test_cockpit.py

A goal routine's ``condition`` is a single command (ADR-0002 §1). When
"green" has to mean more than one thing — the types are clean *and* the
tests that exercise the same code still pass — this is the single
command. Commands are separated by ``--``, run in order from the current
directory, and each one's output is printed under a header so the
proposer sees exactly which part is red. The first failure stops the run.

This file lives in ``scripts/`` and is named by the condition, so the goal
runner protects it twice over; the proposer cannot loosen the check.
"""

from __future__ import annotations

import subprocess
import sys


def split_commands(argv: list[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    current: list[str] = []
    for token in argv:
        if token == "--":
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def main(argv: list[str] | None = None) -> int:
    commands = split_commands(sys.argv[1:] if argv is None else argv)
    if not commands:
        print("goal_check: no commands given (separate them with --)", file=sys.stderr)
        return 2
    for index, command in enumerate(commands, 1):
        # A bare `python` means the interpreter running this check (the goal
        # routine's venv), never whatever PATH happens to resolve.
        if command[0] in ("python", "python3"):
            command = [sys.executable, *command[1:]]
        print(f"=== [{index}/{len(commands)}] {' '.join(command)}", flush=True)
        try:
            proc = subprocess.run(command, check=False)
        except FileNotFoundError as exc:
            print(f"goal_check: cannot run {command[0]}: {exc}", file=sys.stderr)
            return 127
        if proc.returncode != 0:
            print(
                f"=== FAILED [{index}/{len(commands)}] exit {proc.returncode}",
                flush=True,
            )
            return proc.returncode
    print(f"=== all {len(commands)} command(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
