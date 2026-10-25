"""Backlog drain — loop #4 (ADR-0003).

    py scripts/routines/backlog_drain.py              # run the next eligible entry
    py scripts/routines/backlog_drain.py --list       # show the queue, run nothing
    py scripts/routines/backlog_drain.py --dry-run    # say which entry would run
    py scripts/routines/backlog_drain.py --probe NAME # clone/setup/condition only
    py scripts/routines/backlog_drain.py --only NAME  # force one entry, any state

``config/backlog.yaml`` is an ordered list of goal specs (the ``kind: goal``
fields of a routine, plus ``task:`` and ``after:``). Each fire picks **one**
entry and runs it through ``orchestrator.goal_runner.run_goal`` exactly as
a standalone goal routine would, with state under
``$JARVIS_DATA_DIR/routines/backlog-<entry>/`` and events under
``goal:backlog-<entry>``. The per-repo lock is shared with the standalone
goal routines, so the drain never races them.

Which entry, in file order (ADR-0003 §3):

* ``pending`` (never run) — always eligible;
* ``green`` (a patch is waiting for a person) — only when the repo's
  ``base`` has moved since the run, so the patch is rebuilt, never
  re-discovered;
* ``parked`` (stalled / budget / too_large / error) — only when the entry
  itself was edited (its spec hash changed);
* ``landed`` (the runner reported ``already_green``) — never again.

An entry is also held while any of its ``after:`` entries is not landed.

After a green run the drain — never the proposer — ticks the entry's
``task:`` line in ``TASKS.md`` on the goal branch, amends the squashed
commit and regenerates the patch (§4). Nothing here pushes.

State (``$JARVIS_ROUTINE_STATE``, set by run_routine.py)::

    queue.json      one record per entry: state, base, spec, last_run, ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator import goal_runner
from orchestrator.routines import (
    DEFAULT_BACKLOG_PATH,
    BacklogEntry,
    Routine,
    RoutineConfigError,
    load_backlog,
    routine_state_dir,
)

ROUTINE = "backlog-drain"
STATES = ("pending", "green", "landed", "parked")
OUTCOME_STATE = {"green": "green", "already_green": "landed"}  # else parked


@dataclass
class Record:
    state: str = "pending"
    base: str = ""  # sha the last run started from
    spec: str = ""  # hash of the entry as written
    last_run: str = ""
    outcome: str = ""
    branch: str = ""
    patch: str = ""
    ticked: bool = False


@dataclass
class Queue:
    entries: dict[str, Record] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Queue:
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            entries={
                name: Record(
                    **{k: v for k, v in rec.items() if k in Record.__annotations__}
                )
                for name, rec in raw.get("entries", {}).items()
            }
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"entries": {n: asdict(r) for n, r in self.entries.items()}},
                indent=2,
            ),
            encoding="utf-8",
        )

    def record(self, name: str) -> Record:
        return self.entries.setdefault(name, Record())


def spec_hash(entry: BacklogEntry) -> str:
    text = json.dumps(entry.raw, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def remote_base(repo: str, base: str) -> str:
    """Tip of ``base`` on the remote, without a clone. '' if unreachable."""

    try:
        out = subprocess.run(
            ["git", "ls-remote", "--quiet", repo, f"refs/heads/{base}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0 or not out.stdout.strip():
        return ""
    return out.stdout.split()[0]


def entry_state_dir(drain_state: Path, entry: BacklogEntry) -> Path:
    """Sibling of the drain's own state dir, named like a routine, so the
    goal runner's per-repo lock (``<routines>/.locks``) is the shared one."""

    path = drain_state.parent / entry.routine_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_routine(entry: BacklogEntry) -> Routine:
    return Routine(
        name=entry.routine_name,
        description=entry.description or f"backlog: {entry.name}",
        schedule="",
        kind="goal",
        script="orchestrator/goal_runner.py",
        args=(entry.routine_name,),
        timeout_minutes=entry.goal.budget["minutes"] + 5,
        goal=entry.goal,
    )


def eligibility(
    entry: BacklogEntry, record: Record | None, queue: Queue, base_tip: str
) -> tuple[bool, str]:
    """(eligible, reason). ``base_tip`` is the remote's current base sha."""

    for dep in entry.after:
        dep_state = queue.entries.get(dep, Record()).state
        if dep_state != "landed":
            return False, f"after {dep} ({dep_state})"
    if record is None or record.state == "pending":
        return True, "pending"
    if record.state == "landed":
        return False, "landed"
    if record.state == "green":
        if not base_tip:
            return False, "green; remote unreachable"
        if base_tip != record.base:
            return True, f"green; base moved {record.base[:9]} -> {base_tip[:9]}"
        return False, f"green; patch waiting ({Path(record.patch).name or '-'})"
    if record.state == "parked":
        if spec_hash(entry) != record.spec:
            return True, "parked; entry edited"
        return False, f"parked ({record.outcome})"
    return False, f"unknown state {record.state!r}"


def pick(
    entries: list[BacklogEntry], queue: Queue, lookup_base: Any = remote_base
) -> tuple[BacklogEntry | None, list[tuple[str, str]]]:
    """First eligible entry in file order, plus (name, reason) for every
    entry considered."""

    notes: list[tuple[str, str]] = []
    chosen: BacklogEntry | None = None
    for entry in entries:
        record = queue.entries.get(entry.name)
        tip = ""
        if record is not None and record.state == "green":
            tip = lookup_base(entry.goal.repo, entry.goal.base)
        ok, why = eligibility(entry, record, queue, tip)
        notes.append((entry.name, why))
        if ok and chosen is None:
            chosen = entry
    return chosen, notes


def tick_task(
    checkout: Path, branch: str, base: str, task: str, patch: Path, *, today: str
) -> str:
    """Tick ``- [ ] <task>`` in TASKS.md on the goal branch and amend the
    squashed commit; regenerate ``patch``. Returns a one-line note."""

    g = goal_runner.git
    g(["checkout", "-q", branch], cwd=checkout)
    try:
        tasks = checkout / "TASKS.md"
        if not tasks.is_file():
            return "task not ticked: no TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        needle = f"- [ ] {task}"
        hits = [ln for ln in text.splitlines() if ln.strip() == needle]
        if len(hits) != 1:
            return f"task not ticked: {len(hits)} match(es) for {needle!r}"
        done = f"- [x] {task} — DONE {today} ({ROUTINE})"
        lines = [done if ln.strip() == needle else ln for ln in text.splitlines()]
        tasks.write_text(
            "\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8"
        )
        g(["add", "--", "TASKS.md"], cwd=checkout)
        g(["commit", "-q", "--amend", "--no-edit"], cwd=checkout)
        patch.write_text(
            g(["format-patch", "-1", "--stdout", "HEAD"], cwd=checkout).stdout,
            encoding="utf-8",
            newline="\n",
        )
        return "task ticked in TASKS.md"
    finally:
        g(["checkout", "-q", "--detach", base], cwd=checkout, check=False)


def drain_once(
    entries: list[BacklogEntry],
    state: Path,
    *,
    only: str | None = None,
    dry_run: bool = False,
    client: goal_runner.ModelClient | None = None,
    lookup_base: Any = remote_base,
) -> tuple[str, int]:
    """One fire of the drain. Returns (summary line, exit code)."""

    queue_path = state / "queue.json"
    queue = Queue.load(queue_path)
    names = {e.name for e in entries}
    for stale in [n for n in queue.entries if n not in names]:
        del queue.entries[stale]  # the entry was removed from backlog.yaml

    if only is not None:
        entry = next((e for e in entries if e.name == only), None)
        if entry is None:
            return f"backlog: no entry named '{only}'", 2
        notes: list[tuple[str, str]] = [(only, "forced")]
    else:
        entry, notes = pick(entries, queue, lookup_base)

    for name, why in notes:
        print(f"  {name}: {why}")
    if entry is None:
        counts = {s: 0 for s in STATES}
        for e in entries:
            counts[queue.entries.get(e.name, Record()).state] += 1
        queue.save(queue_path)
        return (
            f"backlog: nothing eligible ({counts['landed']} landed, "
            f"{counts['green']} awaiting landing, {counts['parked']} parked, "
            f"{counts['pending']} pending)"
        ), 0
    if dry_run:
        return f"backlog: would run {entry.name}", 0

    routine = build_routine(entry)
    entry_state = entry_state_dir(state, entry)
    print(f"running {entry.routine_name} ({entry.goal.repo} @ {entry.goal.base})")
    result = goal_runner.run_goal(routine, state=entry_state, client=client)

    record = queue.record(entry.name)
    record.state = OUTCOME_STATE.get(result.outcome, "parked")
    record.base = result.base
    record.spec = spec_hash(entry)
    record.last_run = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    record.outcome = result.outcome
    record.branch = result.branch if result.commit else ""
    record.patch = result.patch
    record.ticked = False
    note = ""
    if result.outcome == "green" and entry.task and result.patch:
        note = tick_task(
            entry_state / "repo",
            result.branch,
            result.base,
            entry.task,
            Path(result.patch),
            today=record.last_run[:10],
        )
        record.ticked = note.startswith("task ticked")
    queue.save(queue_path)

    bits = [f"backlog: {entry.name} -> {result.outcome}"]
    if result.iterations:
        bits.append(f"{result.iterations} iteration(s)")
    if result.patch:
        bits.append(f"patch {Path(result.patch).name}")
    if note:
        bits.append(note)
    if result.error:
        bits.append(result.error[:160])
    return " — ".join(bits), (1 if result.outcome == "error" else 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backlog", default=DEFAULT_BACKLOG_PATH, type=Path)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument(
        "--list", action="store_true", help="Show the queue; run nothing."
    )
    parser.add_argument("--dry-run", action="store_true", help="Say what would run.")
    parser.add_argument("--probe", metavar="NAME", help="Clone/setup/condition only.")
    parser.add_argument(
        "--only", metavar="NAME", help="Run this entry whatever its state."
    )
    args = parser.parse_args(argv)

    try:
        entries = load_backlog(args.backlog)
    except RoutineConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    state_env = os.environ.get("JARVIS_ROUTINE_STATE")
    state = args.state or (Path(state_env) if state_env else routine_state_dir(ROUTINE))
    state.mkdir(parents=True, exist_ok=True)

    if args.probe:
        entry = next((e for e in entries if e.name == args.probe), None)
        if entry is None:
            print(f"ERROR: no backlog entry named '{args.probe}'", file=sys.stderr)
            return 2
        result = goal_runner.run_goal(
            build_routine(entry), state=entry_state_dir(state, entry), probe=True
        )
        print(f"condition: {result.condition}")
        print(result.last_output or "(no output)")
        if result.outcome == "probe":
            print(
                f"{entry.routine_name}: probe — condition is red; "
                f"a run would start from {result.base[:9]}"
            )
            return 0
        print(f"{entry.routine_name}: probe — {result.outcome}: {result.error}")
        return 0 if result.outcome == "already_green" else 1

    if args.list:
        queue = Queue.load(state / "queue.json")
        _, notes = pick(entries, queue)
        for name, why in notes:
            print(f"{name}: {why}")
        return 0

    summary, code = drain_once(entries, state, only=args.only, dry_run=args.dry_run)
    print(summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
