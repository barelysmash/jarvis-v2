"""Routine registry — the time-based loop.

Routines are the scheduled, unattended jobs JARVIS runs. They are declared
in ``config/routines.yaml`` (one entry per routine, mirroring the shape of
``agents.yaml``) and realised as systemd user timers on guildenstern.

This module owns three things:

* loading and validating the registry (:func:`load_routines`),
* rendering each routine into a ``jarvis-routine-<name>.service`` /
  ``.timer`` pair (:func:`render_service`, :func:`render_timer`), and
* the on-disk layout a run writes to (:func:`routine_state_dir`).

Executing a routine is ``scripts/run_routine.py``'s job; generating the
units into ``deploy/systemd`` is ``scripts/gen_routine_units.py``'s. Both
are thin wrappers over this module so the tests can pin the behaviour
without touching systemd or spawning anything.

Only ``kind: shell`` exists today: the routine is a script run as a
subprocess with a hard timeout, no brain, no shared session. ``kind: goal``
is reserved for the GoalRunner (TASKS.md, "Loop automation") and is
rejected by the loader until that lands, so a typo cannot silently
schedule nothing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Final

import yaml

DEFAULT_CONFIG_PATH: Final[str] = "config/routines.yaml"
UNIT_PREFIX: Final[str] = "jarvis-routine-"
KINDS: Final[frozenset[str]] = frozenset({"shell"})
RESERVED_KINDS: Final[frozenset[str]] = frozenset({"goal"})

# A routine name is a unit-file stem, a directory name and a cockpit label,
# so keep it to the characters all three agree on.
NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


class RoutineConfigError(ValueError):
    """The routine registry is malformed."""


@dataclass(frozen=True, slots=True)
class Routine:
    """One validated entry from ``routines.yaml``."""

    name: str
    description: str
    schedule: str
    kind: str
    script: str
    args: tuple[str, ...] = ()
    timeout_minutes: int = 30
    jitter_seconds: int = 0
    enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def service_name(self) -> str:
        return f"{UNIT_PREFIX}{self.name}.service"

    @property
    def timer_name(self) -> str:
        return f"{UNIT_PREFIX}{self.name}.timer"


def load_routines(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Routine]:
    """Load and validate every routine, in declaration order.

    Raises :class:`RoutineConfigError` on the first problem found. A
    registry that fails to load schedules nothing — that is the point: a
    broken file should break ``gen_routine_units.py --check`` in CI, not
    quietly drop a timer on the next deploy.
    """

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RoutineConfigError(f"routine registry not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise RoutineConfigError(f"{config_path}: invalid YAML: {exc}") from exc

    if not isinstance(raw, dict) or "routines" not in raw:
        raise RoutineConfigError(
            f"{config_path}: top-level 'routines:' mapping required"
        )

    entries = raw["routines"]
    if entries is None:
        return {}
    if not isinstance(entries, dict):
        raise RoutineConfigError(f"{config_path}: 'routines:' must be a mapping")

    routines: dict[str, Routine] = {}
    for name, spec in entries.items():
        routines[str(name)] = _parse_routine(str(name), spec, config_path)
    return routines


def _parse_routine(name: str, spec: object, config_path: Path) -> Routine:
    where = f"{config_path}: routine '{name}'"

    if not NAME_PATTERN.match(name):
        raise RoutineConfigError(
            f"{where}: name must match {NAME_PATTERN.pattern} "
            "(lowercase, digits, hyphens; unit-file safe)"
        )
    if not isinstance(spec, dict):
        raise RoutineConfigError(f"{where}: must be a mapping")

    kind = _required_str(spec, "kind", where)
    if kind in RESERVED_KINDS:
        raise RoutineConfigError(
            f"{where}: kind '{kind}' is reserved and not implemented yet"
        )
    if kind not in KINDS:
        raise RoutineConfigError(
            f"{where}: kind must be one of {sorted(KINDS)}, got '{kind}'"
        )

    script = _required_str(spec, "script", where)
    if (
        PurePosixPath(script).is_absolute()
        or PureWindowsPath(script).is_absolute()
        or ".." in PurePosixPath(script).parts
        or ".." in PureWindowsPath(script).parts
    ):
        raise RoutineConfigError(
            f"{where}: script must be a repository-relative path, got '{script}'"
        )

    args_raw = spec.get("args", [])
    if args_raw is None:
        args_raw = []
    if not isinstance(args_raw, list) or not all(
        isinstance(a, str | int | float) for a in args_raw
    ):
        raise RoutineConfigError(f"{where}: args must be a list of scalars")

    description = " ".join(_required_str(spec, "description", where).split())
    schedule = _required_str(spec, "schedule", where)
    if "\n" in schedule or ";" in schedule:
        raise RoutineConfigError(f"{where}: schedule must be a single OnCalendar value")

    timeout = _int_field(spec, "timeout_minutes", 30, where, minimum=1)
    jitter = _int_field(spec, "jitter_seconds", 0, where, minimum=0)

    enabled = spec.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RoutineConfigError(f"{where}: enabled must be true or false")

    known = {
        "description",
        "schedule",
        "kind",
        "script",
        "args",
        "timeout_minutes",
        "jitter_seconds",
        "enabled",
    }
    extra = {k: v for k, v in spec.items() if k not in known}

    return Routine(
        name=name,
        description=description,
        schedule=schedule,
        kind=kind,
        script=script,
        args=tuple(str(a) for a in args_raw),
        timeout_minutes=timeout,
        jitter_seconds=jitter,
        enabled=enabled,
        extra=extra,
    )


def _required_str(spec: dict[str, Any], key: str, where: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RoutineConfigError(f"{where}: '{key}' is required and must be a string")
    return value.strip()


def _int_field(
    spec: dict[str, Any], key: str, default: int, where: str, *, minimum: int
) -> int:
    value: object = spec.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoutineConfigError(f"{where}: '{key}' must be an integer >= {minimum}")
    number = int(value)
    if number < minimum:
        raise RoutineConfigError(f"{where}: '{key}' must be an integer >= {minimum}")
    return number


# ─── State on disk ───────────────────────────────────────────


def data_dir() -> Path:
    return Path(os.environ.get("JARVIS_DATA_DIR", "data"))


def routine_state_dir(name: str) -> Path:
    """Where a routine keeps its runs and any state it carries between them.

    ``$JARVIS_DATA_DIR/routines/<name>/`` — outside the release directory,
    so deploys and rollbacks never touch it.
    """

    path = data_dir() / "routines" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─── systemd units ───────────────────────────────────────────

GENERATED_BANNER: Final[str] = (
    "# Generated from config/routines.yaml by scripts/gen_routine_units.py.\n"
    "# Do not edit by hand: change the registry and regenerate.\n"
)


def render_service(routine: Routine) -> str:
    """The oneshot service a routine's timer triggers.

    Mirrors ``jarvis-briefing.service`` deliberately: same working
    directory, same env file, same venv, logs appended under
    ``~/jarvis-data/logs``. ``JARVIS_PROC`` labels rows in the event log so
    the cockpit shows ``routine`` as the writing process.
    """

    log_stem = f"routine-{routine.name}"
    return (
        f"{GENERATED_BANNER}"
        "[Unit]\n"
        f"Description=JARVIS routine: {routine.name} — {routine.description}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "WorkingDirectory=%h/jarvis\n"
        "EnvironmentFile=%h/jarvis-data/env/jarvis.env\n"
        "Environment=JARVIS_PROC=routine\n"
        f"ExecStart=%h/jarvis-venv/bin/python scripts/run_routine.py {routine.name}\n"
        f"TimeoutStartSec={routine.timeout_minutes + 1}min\n"
        f"StandardOutput=append:%h/jarvis-data/logs/{log_stem}.log\n"
        f"StandardError=append:%h/jarvis-data/logs/{log_stem}.err.log\n"
    )


def render_timer(routine: Routine) -> str:
    lines = [
        GENERATED_BANNER.rstrip("\n"),
        "[Unit]",
        f"Description=JARVIS routine timer: {routine.name}",
        "",
        "[Timer]",
        f"OnCalendar={routine.schedule}",
        "Persistent=true",
    ]
    if routine.jitter_seconds:
        lines.append(f"RandomizedDelaySec={routine.jitter_seconds}")
    lines += [
        f"Unit={routine.service_name}",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ]
    return "\n".join(lines)


def render_units(routines: dict[str, Routine]) -> dict[str, str]:
    """``{unit file name: contents}`` for every routine, enabled or not.

    Disabled routines still get units so a flip of ``enabled`` is a one-line
    change; ``remote_install.sh`` reads ``enabled`` through the generated
    ``.timer`` file's presence of ``# jarvis-routine-enabled: false`` marker.
    """

    units: dict[str, str] = {}
    for routine in routines.values():
        units[routine.service_name] = render_service(routine)
        timer = render_timer(routine)
        if not routine.enabled:
            timer = f"# jarvis-routine-enabled: false\n{timer}"
        units[routine.timer_name] = timer
    return units
