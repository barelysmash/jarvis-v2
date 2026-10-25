"""Goal Runner — run-to-green (ADR-0002).

    python orchestrator/goal_runner.py <routine-name>            # a run
    python orchestrator/goal_runner.py <routine-name> --probe    # condition only

Iterates on a repository until the routine's *condition command* exits 0,
or a budget runs out, then hands off a branch and a patch. The condition is
the only judge. The only model is the proposer, which can read files, list
files, write files inside the routine's ``paths:`` allowlist (never the
protected set), and run commands from the routine's named ``commands:``.
There is no shell.

This is deliberately not :class:`JarvisBrain`: no personality, no
``MemoryStore``, no shared conversation. It emits the same ``turn.start`` /
``llm`` / ``tool`` / ``turn.end`` events the brain does, with origin
``goal:<routine>``, so the cockpit shows it, and one ``agent`` event with
the outcome at the end.

Layout under the routine's state dir (``$JARVIS_ROUTINE_STATE``)::

    venv/                     private interpreter for setup/condition/commands
    repo/                     shallow clone; HEAD detached between runs
    repo/.goal-setup          hash of the setup commands last run here
    runs/<stamp>/transcript.jsonl   every model and tool call
    runs/<stamp>/result.json        the outcome
    propagation/<name>-<stamp>.patch
    ../.locks/<repo-url-hash>       one run per target repository
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
import venv
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final, Protocol, TypeGuard, cast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator import event_log
from orchestrator.routines import (
    DEFAULT_CONFIG_PATH,
    GoalSpec,
    Routine,
    RoutineConfigError,
    load_routines,
    routine_state_dir,
)

DEFAULT_MODEL: Final[str] = os.environ.get("JARVIS_GOAL_MODEL", "claude-opus-4-7")
MAX_TOOL_CALLS_PER_ITERATION: Final[int] = 40
MAX_MODEL_OUTPUT_TOKENS: Final[int] = 8_000
COMMAND_TIMEOUT_SECONDS: Final[int] = 300
OUTPUT_CLIP: Final[int] = 12_000  # chars of command output the model sees
FILE_CLIP: Final[int] = 60_000  # chars of a file the model sees
STALL_REPEATS: Final[int] = 3
GIT_IDENTITY: Final[dict[str, str]] = {
    "GIT_AUTHOR_NAME": "JARVIS goal-runner",
    "GIT_AUTHOR_EMAIL": "jarvis@barelysmash.local",
    "GIT_COMMITTER_NAME": "JARVIS goal-runner",
    "GIT_COMMITTER_EMAIL": "jarvis@barelysmash.local",
}

# ADR-0002 §4: what the proposer may never write, before `unprotect:`.
PROTECTED_DIR_PARTS: Final[frozenset[str]] = frozenset(
    {".git", ".github", "tests", "test", "deploy", "scripts"}
)
PROTECTED_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "tox.ini",
        ".pre-commit-config.yaml",
        "jam.yaml",
        "VERSION",
        "requirements.txt",
        "requirements-dev.txt",
    }
)
PROTECTED_SUFFIXES: Final[tuple[str, ...]] = (".toml", ".ini", ".schema.json")

OUTCOMES: Final[tuple[str, ...]] = (
    "green",
    "already_green",
    "stalled",
    "budget",
    "too_large",
    "error",
    "probe",
)


class GoalError(RuntimeError):
    """The run could not do its job (outcome ``error``)."""


# ─── Model client (swappable; tests inject a fake) ──────────


@dataclass(slots=True)
class ModelReply:
    content: list[
        dict[str, Any]
    ]  # {"type": "text", "text"} | {"type": "tool_use", "id", "name", "input"}
    stop_reason: str
    input_tokens: int
    output_tokens: int
    model: str


class ModelClient(Protocol):
    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> ModelReply: ...


class AnthropicClient:
    """Thin adapter over the Anthropic SDK producing :class:`ModelReply`.

    The SDK's response blocks are a wide union; the two we act on are
    recognised by their ``type`` tag (duck-typed, so tests can hand in
    plain objects) and narrowed for the type checker with TypeGuards.
    """

    def __init__(self, api_key: str | None = None) -> None:
        import anthropic  # runtime dependency; not installed at type-check time

        self._client = anthropic.Anthropic(api_key=api_key)

    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> ModelReply:
        # The SDK types these as TypedDict unions; our dicts are built to that
        # shape (role/content; name/description/input_schema). Cast at the edge.
        response = self._client.messages.create(
            model=model,
            system=system,
            messages=cast(Any, messages),
            tools=cast(Any, tools),
            max_tokens=max_tokens,
        )
        content: list[dict[str, Any]] = []
        for block in response.content:
            if _is_text_block(block):
                content.append({"type": "text", "text": block.text})
            elif _is_tool_use_block(block):
                raw_input = block.input
                content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": dict(raw_input) if isinstance(raw_input, dict) else {},
                    }
                )
        usage = getattr(response, "usage", None)
        return ModelReply(
            content=content,
            stop_reason=str(getattr(response, "stop_reason", "") or ""),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            model=str(getattr(response, "model", model)),
        )


class _TextLike(Protocol):
    type: str
    text: str


class _ToolUseLike(Protocol):
    type: str
    id: str
    name: str
    input: object


def _is_text_block(block: object) -> TypeGuard[_TextLike]:
    return getattr(block, "type", None) == "text" and hasattr(block, "text")


def _is_tool_use_block(block: object) -> TypeGuard[_ToolUseLike]:
    return getattr(block, "type", None) == "tool_use" and hasattr(block, "name")


# ─── Git / shell helpers ─────────────────────────────────────


def git(
    args: list[str], *, cwd: Path, check: bool = True, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    env.update(GIT_IDENTITY)
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        raise GoalError(
            f"git {' '.join(args)} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:600]}"
        )
    return proc


@dataclass(slots=True)
class CommandResult:
    name: str
    command: str
    exit: int
    output: str  # stdout + stderr, in order of streams, clipped for storage
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit == 0 and not self.timed_out


def run_command(
    name: str,
    command: str,
    cwd: Path,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> CommandResult:
    started = time.time()
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        return CommandResult(name, command, 127, f"cannot parse command: {exc}", 0.0)
    if not argv:
        return CommandResult(name, command, 127, "empty command", 0.0)
    # A bare `python` must be the routine's interpreter. PATH ordering is
    # not enough on Windows (the App Execution Alias wins), so resolve it.
    if env and argv[0] in ("python", "python3") and env.get("JARVIS_GOAL_PYTHON"):
        argv[0] = env["JARVIS_GOAL_PYTHON"]
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**(env or os.environ), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        return CommandResult(
            name, command, proc.returncode, output.strip(), time.time() - started
        )
    except FileNotFoundError as exc:
        return CommandResult(
            name, command, 127, f"not found: {exc}", time.time() - started
        )
    except subprocess.TimeoutExpired as exc:
        out = _text(exc.stdout) + ("\n" + _text(exc.stderr) if exc.stderr else "")
        return CommandResult(
            name, command, 124, out.strip(), time.time() - started, timed_out=True
        )


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n… [{len(text) - limit} chars elided] …\n{text[-tail:]}"


def normalize_for_stall(output: str) -> str:
    """Condition output with the parts that legitimately vary stripped."""

    text = re.sub(r"\d+\.\d+ ?s\b", "", output)  # durations
    text = re.sub(r"0x[0-9a-fA-F]+", "", text)  # addresses
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(text.encode()).hexdigest()


# ─── Checkout ────────────────────────────────────────────────


def ensure_venv(state: Path, *, with_pip: bool) -> dict[str, str]:
    """A private interpreter for the routine's commands, so ``setup`` can
    ``pip install`` without touching the production jarvis-venv. Returns the
    environment to run commands with: the venv first on PATH, so ``python``
    in a condition or command means this one."""

    venv_dir = state / "venv"
    bindir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    pip_present = (bindir / ("pip.exe" if os.name == "nt" else "pip")).exists()
    if not python.exists() or (with_pip and not pip_present):
        venv.EnvBuilder(with_pip=with_pip, clear=python.exists()).create(venv_dir)
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(venv_dir)
    env["JARVIS_GOAL_PYTHON"] = str(python)
    env.pop("PYTHONHOME", None)
    return env


def ensure_checkout(
    state: Path, spec: GoalSpec, env: dict[str, str] | None = None
) -> tuple[Path, str]:
    """Shallow clone of ``spec.repo`` at ``spec.base``, reset to the tip,
    HEAD detached. Runs ``setup`` once per distinct setup list. Returns
    (path, base sha)."""

    path = state / "repo"
    if (path / ".git").is_dir():
        origin = git(["remote", "get-url", "origin"], cwd=path, check=False)
        if origin.stdout.strip() != spec.repo:
            import shutil

            shutil.rmtree(path)
    if (path / ".git").is_dir():
        git(["fetch", "--depth", "1", "--quiet", "origin", spec.base], cwd=path)
        git(["checkout", "--quiet", "--detach"], cwd=path, check=False)
        git(["reset", "--quiet", "--hard", "FETCH_HEAD"], cwd=path)
        git(["clean", "-fdq", "-e", ".goal-setup"], cwd=path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        git(
            [
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--branch",
                spec.base,
                spec.repo,
                str(path),
            ],
            cwd=state,
            timeout=900,
        )
        git(["checkout", "--quiet", "--detach"], cwd=path)
    base = git(["rev-parse", "HEAD"], cwd=path).stdout.strip()

    marker = path / ".goal-setup"
    wanted = (
        hashlib.sha256("\n".join(spec.setup).encode()).hexdigest() if spec.setup else ""
    )
    if spec.setup and (not marker.is_file() or marker.read_text().strip() != wanted):
        for i, command in enumerate(spec.setup, 1):
            result = run_command(f"setup-{i}", command, path, timeout=900, env=env)
            if not result.ok:
                raise GoalError(
                    f"setup command {i} failed ({result.exit}): {command}\n"
                    f"{clip(result.output, 800)}"
                )
        marker.write_text(wanted + "\n")
    # The marker is untracked and must never ride along in a commit.
    exclude = path / ".git" / "info" / "exclude"
    if exclude.is_file() and ".goal-setup" not in exclude.read_text():
        exclude.write_text(exclude.read_text().rstrip("\n") + "\n.goal-setup\n")
    return path, base


# ─── Sandbox: what the proposer can touch ────────────────────


def _glob_to_fnmatch(pattern: str) -> str:
    # fnmatch's * already crosses "/", so ** and * mean the same thing here.
    return pattern.replace("**", "*")


class Sandbox:
    """Path policy and tool execution for one run."""

    def __init__(
        self,
        checkout: Path,
        spec: GoalSpec,
        base: str,
        env: dict[str, str] | None = None,
    ) -> None:
        self.checkout = checkout
        self.spec = spec
        self.base = base
        self.env = env
        self.condition_files = self._condition_files()
        self.writes: list[str] = []
        self.too_large_hit = False

    # -- policy --

    def _condition_files(self) -> frozenset[str]:
        files = set()
        for token in shlex.split(self.spec.condition):
            posix = PurePosixPath(token.replace("\\", "/"))
            if posix.is_absolute() or ".." in posix.parts:
                continue  # the interpreter, a flag, or something outside the repo
            candidate = self.checkout / posix
            if candidate.is_file():
                files.add(posix.as_posix())
        return frozenset(files)

    def relative(self, raw: str) -> str:
        """Normalise a model-supplied path to a repo-relative posix path."""

        if not raw or raw.strip() != raw:
            raise GoalError(f"bad path: {raw!r}")
        posix = PurePosixPath(raw.replace("\\", "/"))
        if (
            posix.is_absolute()
            or re.match(r"^[A-Za-z]:", raw)  # Windows drive letter
            or ".." in posix.parts
            or posix.parts[:1] == (".git",)
        ):
            raise GoalError(
                f"path must be repo-relative and inside the checkout: {raw}"
            )
        return posix.as_posix()

    def is_protected(self, rel: str) -> bool:
        if any(fnmatch.fnmatch(rel, _glob_to_fnmatch(g)) for g in self.spec.unprotect):
            return False
        parts = PurePosixPath(rel).parts
        name = parts[-1]
        if rel in self.condition_files:
            return True
        if any(part in PROTECTED_DIR_PARTS for part in parts[:-1]):
            return True
        if name in PROTECTED_BASENAMES or name.endswith(PROTECTED_SUFFIXES):
            return True
        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or name == "conftest.py"
        )

    def is_allowed(self, rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, _glob_to_fnmatch(g)) for g in self.spec.paths)

    def changed_lines(self) -> int:
        git(["add", "-A", "--", "."], cwd=self.checkout)
        out = git(
            ["diff", "--cached", "--numstat", self.base], cwd=self.checkout
        ).stdout
        total = 0
        for line in out.splitlines():
            added, _, rest = line.partition("\t")
            deleted, _, _ = rest.partition("\t")
            if added.isdigit():
                total += int(added)
            if deleted.isdigit():
                total += int(deleted)
        return total

    # -- tools --

    def tool_read_file(self, path: str) -> str:
        rel = self.relative(path)
        target = self.checkout / rel
        if not target.is_file():
            return f"ERROR: no such file: {rel}"
        try:
            return clip(target.read_text(encoding="utf-8"), FILE_CLIP)
        except UnicodeDecodeError:
            return f"ERROR: {rel} is not a text file"

    def tool_list_files(self, pattern: str) -> str:
        pat = _glob_to_fnmatch(pattern.strip() or "*")
        out = git(
            ["ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=self.checkout,
        ).stdout
        matches = [p for p in out.splitlines() if fnmatch.fnmatch(p, pat)]
        if not matches:
            return f"(no files match {pattern})"
        return "\n".join(matches[:500]) + (
            "\n… (truncated)" if len(matches) > 500 else ""
        )

    def tool_write_file(self, path: str, content: str) -> str:
        rel = self.relative(path)
        if self.is_protected(rel):
            return (
                f"REFUSED: {rel} is protected (tests, CI/tool config, contracts, "
                "or a file the condition names). Fix the code the condition "
                "checks instead."
            )
        if not self.is_allowed(rel):
            return (
                f"REFUSED: {rel} is outside this routine's edit scope "
                f"({', '.join(self.spec.paths)})."
            )
        target = self.checkout / rel
        previous = target.read_text(encoding="utf-8") if target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        total = self.changed_lines()
        if total > self.spec.max_changed_lines:
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(previous, encoding="utf-8", newline="\n")
            self.changed_lines()  # re-stage the restored state
            self.too_large_hit = True
            return (
                f"REFUSED: that write would bring the branch to {total} changed lines, "
                f"over the {self.spec.max_changed_lines}-line limit for an unattended "
                "change. The run will stop here."
            )
        if rel not in self.writes:
            self.writes.append(rel)
        return (
            f"wrote {rel} ({len(content.splitlines())} lines; "
            f"branch now {total} changed lines)"
        )

    def tool_run(self, name: str) -> str:
        command = self.spec.commands.get(name)
        if command is None:
            return (
                f"ERROR: no command named {name!r}. "
                f"Available: {', '.join(sorted(self.spec.commands))}"
            )
        result = run_command(name, command, self.checkout, env=self.env)
        status = "timed out" if result.timed_out else f"exit {result.exit}"
        return (
            f"$ {command}\n[{status}, {result.seconds:.1f}s]\n"
            f"{clip(result.output, OUTPUT_CLIP)}"
        )

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "read_file":
                return self.tool_read_file(str(args.get("path", "")))
            if name == "list_files":
                return self.tool_list_files(str(args.get("pattern", "*")))
            if name == "write_file":
                return self.tool_write_file(
                    str(args.get("path", "")), str(args.get("content", ""))
                )
            if name == "run":
                return self.tool_run(str(args.get("name", "")))
            return f"ERROR: unknown tool {name}"
        except GoalError as exc:
            return f"ERROR: {exc}"


TOOLS: Final[list[dict[str, Any]]] = [
    {
        "name": "read_file",
        "description": "Read a file from the checkout (repo-relative path).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List tracked files matching a glob (e.g. 'src/**/*.py').",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Replace a file's full contents. Only paths inside the edit scope; "
            "tests, CI/tool config and contract files are refused."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "run",
        "description": "Run one of the routine's named commands and return its output.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
]


# ─── The loop ────────────────────────────────────────────────


@dataclass(slots=True)
class GoalResult:
    routine: str
    outcome: str
    condition: str
    iterations: int = 0
    base: str = ""
    branch: str = ""
    commit: str = ""
    patch: str = ""
    files: list[str] = field(default_factory=list)
    changed_lines: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    last_output: str = ""
    error: str = ""
    run_dir: str = ""

    @property
    def summary(self) -> str:
        bits = [f"{self.routine}: {self.outcome}"]
        if self.iterations:
            bits.append(f"{self.iterations} iteration(s)")
        if self.commit:
            bits.append(
                f"{self.branch}@{self.commit} "
                f"({self.changed_lines} lines, {len(self.files)} file(s))"
            )
        if self.patch:
            bits.append(f"patch {Path(self.patch).name}")
        if self.error:
            bits.append(self.error[:200])
        return " — ".join(bits)


def system_prompt(routine: Routine, spec: GoalSpec, sandbox: Sandbox) -> str:
    return (
        "You are the proposer in an unattended run-to-green loop on a git checkout.\n"
        f"Goal: make this command exit 0:  {spec.condition}\n"
        "You cannot talk to anyone. Each turn: read what you need, make the smallest "
        "edit that moves the condition toward passing, optionally run a named command "
        "to check, then end your turn. The runner commits your edits and re-runs the "
        "condition; you will see its output next turn.\n"
        f"You may write only within: {', '.join(spec.paths)}. Tests, CI and tool "
        "configuration, contract files and the files the condition names are "
        "protected and will be refused; do not try to loosen the check — fix "
        "the code.\n"
        f"Named commands you can run: {', '.join(sorted(spec.commands))}.\n"
        f"Budget: {spec.budget['iterations']} iterations, {spec.max_changed_lines} "
        "changed lines total. Prefer targeted edits; replace whole files only "
        "when small.\n"
        "If the condition cannot be met within the rules (needs a new dependency, a "
        "test change, or a design decision), say so plainly in text and end your turn "
        "without editing; a person will read the transcript."
    )


def first_message(cond: CommandResult, spec: GoalSpec, iteration: int) -> str:
    return (
        f"Iteration {iteration} of {spec.budget['iterations']}.\n"
        f"Condition: {spec.condition}\n"
        f"Exit {cond.exit}{' (timed out)' if cond.timed_out else ''}. Output:\n"
        f"{clip(cond.output, OUTPUT_CLIP) or '(no output)'}"
    )


class Transcript:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, **data: Any) -> None:
        record = {"ts": datetime.now(UTC).isoformat(), "kind": kind, **data}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


def _lock_path(state: Path, repo: str) -> Path:
    locks = state.parent / ".locks"
    locks.mkdir(parents=True, exist_ok=True)
    return locks / hashlib.sha256(repo.encode()).hexdigest()[:16]


def _acquire_lock(path: Path, name: str) -> None:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = path.read_text(errors="replace").strip() if path.is_file() else "?"
        raise GoalError(
            f"another goal run holds the lock for this repository ({holder}); "
            f"remove {path} if it is stale"
        ) from None
    with os.fdopen(fd, "w") as fh:
        fh.write(f"{name} pid={os.getpid()} {datetime.now(UTC).isoformat()}\n")


def run_goal(
    routine: Routine,
    *,
    state: Path,
    client: ModelClient | None = None,
    probe: bool = False,
) -> GoalResult:
    spec = routine.goal
    if spec is None:
        raise GoalError(f"{routine.name} is not a kind: goal routine")
    started = time.time()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = state / "runs" / stamp
    transcript = Transcript(run_dir / "transcript.jsonl")
    result = GoalResult(routine=routine.name, outcome="error", condition=spec.condition)
    result.run_dir = str(run_dir)
    origin = f"goal:{routine.name}"

    lock = _lock_path(state, spec.repo)
    checkout: Path | None = None
    held = False
    try:
        _acquire_lock(lock, routine.name)
        held = True
        env = ensure_venv(state, with_pip=bool(spec.setup))
        checkout, base = ensure_checkout(state, spec, env)
        result.base = base
        transcript.write(
            "start", repo=spec.repo, base=base, condition=spec.condition, probe=probe
        )

        cond = run_command("condition", spec.condition, checkout, env=env)
        transcript.write(
            "condition",
            iteration=0,
            exit=cond.exit,
            seconds=cond.seconds,
            output=cond.output,
        )
        result.last_output = clip(cond.output, 2000)
        if cond.ok:
            result.outcome = "already_green"
            return result
        if probe:
            result.outcome = "probe"
            return result

        model = spec.model or DEFAULT_MODEL
        client = client or AnthropicClient()
        branch = f"goal/{routine.name}"
        result.branch = branch
        git(["checkout", "-q", "-B", branch, base], cwd=checkout)
        sandbox = Sandbox(checkout, spec, base, env)
        system = system_prompt(routine, spec, sandbox)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": first_message(cond, spec, 1)}
        ]
        stall_hashes: list[str] = [normalize_for_stall(cond.output)]
        tokens = 0
        deadline = started + spec.budget["minutes"] * 60
        commits = 0

        for iteration in range(1, spec.budget["iterations"] + 1):
            # Budgets are checked before an iteration starts, so a run that
            # stops here reports the iterations it actually completed.
            if time.time() > deadline:
                result.outcome = "budget"
                result.error = f"wall clock: {spec.budget['minutes']} min"
                break
            if tokens > spec.budget["tokens"]:
                result.outcome = "budget"
                result.error = f"tokens: {tokens} > {spec.budget['tokens']}"
                break
            result.iterations = iteration

            turn_id = uuid.uuid4().hex[:8]
            turn_started = time.time()
            turn_tools = 0
            event_log.emit(
                "goal",
                "turn.start",
                {
                    "turn_id": turn_id,
                    "origin": origin,
                    "model": model,
                    "input": f"iteration {iteration}: condition exit {cond.exit}",
                    "history": len(messages),
                },
            )
            text_out = ""
            for _call in range(MAX_TOOL_CALLS_PER_ITERATION):
                llm_started = time.time()
                reply = client.create(
                    model=model,
                    system=system,
                    messages=messages,
                    tools=TOOLS,
                    max_tokens=MAX_MODEL_OUTPUT_TOKENS,
                )
                tokens += reply.input_tokens + reply.output_tokens
                result.input_tokens += reply.input_tokens
                result.output_tokens += reply.output_tokens
                transcript.write(
                    "llm",
                    iteration=iteration,
                    stop=reply.stop_reason,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    content=reply.content,
                )
                texts = [b["text"] for b in reply.content if b.get("type") == "text"]
                tool_uses = [b for b in reply.content if b.get("type") == "tool_use"]
                event_log.emit(
                    "goal",
                    "llm",
                    {
                        "turn_id": turn_id,
                        "iteration": iteration,
                        "model": reply.model,
                        "ms": int((time.time() - llm_started) * 1000),
                        "stop_reason": reply.stop_reason,
                        "input_tokens": reply.input_tokens,
                        "output_tokens": reply.output_tokens,
                        "text": event_log.clip("\n".join(texts), 2000),
                        "tool_calls": [b["name"] for b in tool_uses],
                    },
                )
                text_out = "\n".join(texts) or text_out
                messages.append({"role": "assistant", "content": reply.content})
                if not tool_uses:
                    break
                results: list[dict[str, Any]] = []
                for use in tool_uses:
                    call_id = str(use.get("id") or uuid.uuid4().hex[:8])
                    name = str(use.get("name"))
                    args = (
                        use.get("input") if isinstance(use.get("input"), dict) else {}
                    )
                    event_log.emit(
                        "goal",
                        "tool",
                        {
                            "name": name,
                            "args": event_log.clip(args, 1000),
                            "status": "running",
                            "turn_id": turn_id,
                            "call_id": call_id,
                            "agent": "goal",
                        },
                    )
                    t0 = time.time()
                    output = sandbox.dispatch(name, dict(args) if args else {})
                    turn_tools += 1
                    status = (
                        "error"
                        if output.startswith(("ERROR", "REFUSED"))
                        else "success"
                    )
                    event_log.emit(
                        "goal",
                        "tool",
                        {
                            "name": name,
                            "args": event_log.clip(args, 1000),
                            "status": status,
                            "turn_id": turn_id,
                            "call_id": call_id,
                            "agent": "goal",
                            "ms": int((time.time() - t0) * 1000),
                            "result": event_log.clip(output, 2000),
                        },
                    )
                    transcript.write(
                        "tool", iteration=iteration, name=name, args=args, output=output
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": output,
                        }
                    )
                    if sandbox.too_large_hit:
                        break
                messages.append({"role": "user", "content": results})
                if sandbox.too_large_hit:
                    break
                if tokens > spec.budget["tokens"] or time.time() > deadline:
                    break

            # Commit whatever this iteration changed.
            changed = sandbox.changed_lines()
            dirty = (
                git(
                    ["diff", "--cached", "--quiet"], cwd=checkout, check=False
                ).returncode
                != 0
            )
            if dirty:
                git(
                    [
                        "commit",
                        "-q",
                        "-m",
                        f"goal {routine.name}: iteration {iteration}",
                    ],
                    cwd=checkout,
                )
                commits += 1
            result.changed_lines = changed
            result.files = list(sandbox.writes)

            cond = run_command("condition", spec.condition, checkout, env=env)
            transcript.write(
                "condition",
                iteration=iteration,
                exit=cond.exit,
                seconds=cond.seconds,
                output=cond.output,
            )
            result.last_output = clip(cond.output, 2000)
            event_log.emit(
                "goal",
                "turn.end",
                {
                    "turn_id": turn_id,
                    "origin": origin,
                    "text": event_log.clip(text_out, 1000),
                    "ms": int((time.time() - turn_started) * 1000),
                    "error": None,
                    "iterations": 1,
                    "tools": turn_tools,
                    "errors": 0,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "condition_exit": cond.exit,
                },
            )

            if sandbox.too_large_hit:
                result.outcome = "too_large"
                break
            if cond.ok:
                result.outcome = "green"
                break
            stall_hashes.append(normalize_for_stall(cond.output))
            if (
                len(stall_hashes) >= STALL_REPEATS
                and len(set(stall_hashes[-STALL_REPEATS:])) == 1
            ):
                result.outcome = "stalled"
                break
            messages.append(
                {"role": "user", "content": first_message(cond, spec, iteration + 1)}
            )
        else:
            result.outcome = "budget"
            result.error = f"iterations: {spec.budget['iterations']}"

        # Hand-off: squash to one commit and write the patch.
        if commits:
            git(["reset", "-q", "--soft", base], cwd=checkout)
            message = (
                f"goal: {routine.name}: {result.outcome}\n\n"
                f"condition: {spec.condition}\n"
                f"outcome: {result.outcome} after {result.iterations} iteration(s)\n"
                f"files: {', '.join(result.files) or '-'}\n"
                f"changed lines: {result.changed_lines}\n\n"
                "Prepared by the JARVIS goal runner (ADR-0002). Transcript:\n"
                f"{run_dir / 'transcript.jsonl'}\n"
            )
            msg_file = run_dir / "commit-msg.txt"
            msg_file.write_text(message, encoding="utf-8")
            git(["commit", "-q", "-F", str(msg_file)], cwd=checkout)
            result.commit = git(
                ["rev-parse", "--short", "HEAD"], cwd=checkout
            ).stdout.strip()
            patch_dir = state / "propagation"
            patch_dir.mkdir(parents=True, exist_ok=True)
            patch = patch_dir / f"{routine.name}-{stamp}.patch"
            patch.write_text(
                git(["format-patch", "-1", "--stdout", "HEAD"], cwd=checkout).stdout,
                encoding="utf-8",
                newline="\n",
            )
            result.patch = str(patch)
        return result
    except GoalError as exc:
        result.outcome = "error"
        result.error = str(exc)
        return result
    finally:
        result.seconds = round(time.time() - started, 1)
        if checkout is not None and (checkout / ".git").is_dir():
            git(
                ["checkout", "-q", "--detach", result.base or "HEAD"],
                cwd=checkout,
                check=False,
            )
            git(["clean", "-fdq", "-e", ".goal-setup"], cwd=checkout, check=False)
        transcript.write(
            "end",
            outcome=result.outcome,
            iterations=result.iterations,
            commit=result.commit,
            patch=result.patch,
            error=result.error,
            seconds=result.seconds,
        )
        (run_dir / "result.json").write_text(
            json.dumps(
                asdict(result) | {"summary": result.summary}, indent=2, default=str
            ),
            encoding="utf-8",
        )
        if result.outcome not in ("probe",):
            event_log.emit(
                routine.name,
                "agent",
                {
                    "agent": routine.name,
                    "kind": "goal",
                    "severity": "notice"
                    if result.outcome in ("green", "already_green")
                    else "warning",
                    "summary": result.summary,
                    "data": {
                        k: v for k, v in asdict(result).items() if k != "last_output"
                    },
                },
            )
        if held:
            lock.unlink(missing_ok=True)


# ─── CLI ─────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, type=Path)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Clone/setup and run the condition once; no model, no branch.",
    )
    parser.add_argument("--state", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        routines = load_routines(args.config)
    except RoutineConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    routine = routines.get(args.name)
    if routine is None or routine.goal is None:
        print(f"ERROR: no kind: goal routine named '{args.name}'", file=sys.stderr)
        return 2
    state_env = os.environ.get("JARVIS_ROUTINE_STATE")
    state = args.state or (
        Path(state_env) if state_env else routine_state_dir(routine.name)
    )
    state.mkdir(parents=True, exist_ok=True)

    result = run_goal(routine, state=state, probe=args.probe)
    if result.outcome == "probe":
        print(f"condition: {result.condition}")
        print(result.last_output or "(no output)")
        print(
            f"{routine.name}: probe — condition is red; "
            f"a run would start from {result.base[:9]}"
        )
        return 0
    print(result.summary)
    return 1 if result.outcome == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
