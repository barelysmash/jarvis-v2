"""Run one routine from config/routines.yaml. Invoked by its systemd unit.

    py scripts/run_routine.py conformance-sweep
    py scripts/run_routine.py conformance-sweep --dry-run   # show the command

What the runner adds around the routine's own script:

* ``event_log.job(<name>)`` bracketing, so the cockpit's Scheduled and
  Agents panes see start/end, duration, success and a summary line;
* a hard timeout (``timeout_minutes`` from the registry) — the script is
  killed and the run recorded as failed;
* a per-run log at ``$JARVIS_DATA_DIR/routines/<name>/runs/<stamp>.log``
  (stdout + stderr, interleaved) and ``last.json`` with the outcome;
* the script's last non-empty stdout line becomes the job summary.

The exit status is the script's. A routine that *found* something
(conformance drift, say) should still exit 0 — findings go in its report.
Non-zero means the routine itself could not do its job, which is what
``Result=failed`` on the unit, and the red row in the cockpit, should mean.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator import event_log
from orchestrator.routines import (
    DEFAULT_CONFIG_PATH,
    Routine,
    RoutineConfigError,
    load_routines,
    routine_state_dir,
)

MAX_RUN_LOGS = 30


def build_command(routine: Routine, repo_root: Path) -> list[str]:
    return [sys.executable, str(repo_root / routine.script), *routine.args]


def summary_line(stdout: str) -> str:
    for line in reversed(stdout.splitlines()):
        if line.strip():
            return line.strip()[:400]
    return "(no output)"


def _text(value: object) -> str:
    """Partial output from a killed subprocess, whatever type it arrived as."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def prune_runs(runs_dir: Path, keep: int = MAX_RUN_LOGS) -> None:
    logs = sorted(runs_dir.glob("*.log"))
    for old in logs[:-keep] if len(logs) > keep else []:
        old.unlink(missing_ok=True)


def run(routine: Routine, repo_root: Path) -> int:
    state = routine_state_dir(routine.name)
    runs_dir = state / "runs"
    runs_dir.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_log = runs_dir / f"{stamp}.log"
    command = build_command(routine, repo_root)

    env = dict(os.environ)
    env.setdefault("JARVIS_PROC", "routine")
    env["JARVIS_ROUTINE"] = routine.name
    env["JARVIS_ROUTINE_STATE"] = str(state)

    outcome: dict[str, object] = {
        "routine": routine.name,
        "started": stamp,
        "command": command,
        "log": str(run_log),
    }
    started = time.time()
    exit_code = 1

    with event_log.job(routine.name, kind=routine.kind, script=routine.script) as rec:
        print(f"[routine:{routine.name}] {' '.join(command)}", flush=True)
        try:
            proc = subprocess.run(
                command,
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=routine.timeout_minutes * 60,
                check=False,
            )
            stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = _text(exc.stdout), _text(exc.stderr)
            exit_code = 124
            timed_out = True

        run_log.write_text(
            f"# {routine.name} {stamp}\n# {' '.join(command)}\n\n"
            f"{stdout}\n--- stderr ---\n{stderr}\n--- exit {exit_code} ---\n",
            encoding="utf-8",
        )
        prune_runs(runs_dir)

        summary = summary_line(stdout)
        if timed_out:
            summary = f"timed out after {routine.timeout_minutes}min; {summary}"
        rec["summary"] = summary
        rec["exit"] = exit_code
        outcome.update(
            {
                "ended": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
                "seconds": round(time.time() - started, 1),
                "exit": exit_code,
                "ok": exit_code == 0,
                "summary": summary,
            }
        )
        (state / "last.json").write_text(
            json.dumps(outcome, indent=2), encoding="utf-8"
        )

        sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
        print(f"[routine:{routine.name}] exit {exit_code} — {summary}", flush=True)
        if exit_code != 0:
            raise RuntimeError(f"{routine.name} exited {exit_code}: {summary}")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    try:
        routines = load_routines(args.config)
    except RoutineConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    routine = routines.get(args.name)
    if routine is None:
        print(
            f"ERROR: no routine '{args.name}' in {args.config} "
            f"(have: {', '.join(routines) or 'none'})",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        print(" ".join(build_command(routine, repo_root)))
        print(f"timeout: {routine.timeout_minutes}min  schedule: {routine.schedule}")
        return 0

    try:
        return run(routine, repo_root)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
