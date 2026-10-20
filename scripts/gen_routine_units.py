"""Render config/routines.yaml into deploy/systemd unit files.

    py scripts/gen_routine_units.py           # write units, report changes
    py scripts/gen_routine_units.py --check   # exit 1 if units are stale (CI)

One ``jarvis-routine-<name>.service`` + ``.timer`` pair per routine. Units
for routines that no longer exist in the registry are removed, so the
registry is the single source of truth and ``deploy/systemd`` is a build
artifact that happens to be committed (the same drift-detection pattern
JAM uses for its category registry).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.routines import (
    DEFAULT_CONFIG_PATH,
    UNIT_PREFIX,
    RoutineConfigError,
    load_routines,
    render_units,
)

DEFAULT_UNIT_DIR = "deploy/systemd"


def plan(
    config: Path, unit_dir: Path
) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """Return (desired units, to write, unchanged, to delete)."""

    desired = render_units(load_routines(config))
    existing = {
        p.name
        for p in unit_dir.glob(f"{UNIT_PREFIX}*")
        if p.suffix in {".service", ".timer"}
    }
    write: list[str] = []
    same: list[str] = []
    for name, body in desired.items():
        path = unit_dir / name
        if path.is_file() and path.read_text(encoding="utf-8") == body:
            same.append(name)
        else:
            write.append(name)
    delete = sorted(existing - desired.keys())
    return desired, write, same, delete


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, type=Path)
    parser.add_argument("--unit-dir", default=DEFAULT_UNIT_DIR, type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; fail if deploy/systemd does not match the registry.",
    )
    args = parser.parse_args(argv)

    try:
        desired, write, same, delete = plan(args.config, args.unit_dir)
    except RoutineConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for name in same:
        print(f"  unchanged  {name}")
    for name in write:
        print(f"  {'stale' if args.check else 'write'}      {name}")
    for name in delete:
        print(f"  {'orphan' if args.check else 'delete'}     {name}")

    if args.check:
        if write or delete:
            print(
                "\nroutine units are out of date: run "
                "`py scripts/gen_routine_units.py` and commit deploy/systemd",
                file=sys.stderr,
            )
            return 1
        print(f"\n{len(desired)} unit file(s) match config/routines.yaml")
        return 0

    args.unit_dir.mkdir(parents=True, exist_ok=True)
    for name in write:
        (args.unit_dir / name).write_text(desired[name], encoding="utf-8", newline="\n")
    for name in delete:
        (args.unit_dir / name).unlink()
    print(f"\n{len(write)} written, {len(same)} unchanged, {len(delete)} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
