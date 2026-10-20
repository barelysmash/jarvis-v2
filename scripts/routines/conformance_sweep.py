"""Conformance sweep — routine #1 of the time-based loop.

    py scripts/routines/conformance_sweep.py \\
        --jam-ref main \\
        --repo atlas=https://github.com/barelysmash/atlas \\
        --repo jarvis-v2=https://github.com/barelysmash/jarvis-v2

For every ``--repo name=url[@branch]``: shallow-clone or fast-forward it
under the routine's state directory, run ``jam validate --json`` against it
with the pinned jam-sdk, and diff the result against the previous sweep.
Nothing is ever written to a repository; the sweep *consumes* JAM, it does
not propose changes to it.

What comes out:

* ``report.json`` in the state dir — the full picture of this run;
* ``history/<repo>/latest.json`` (+ dated copies) — what the next run
  diffs against;
* one ``agent`` event in the JARVIS event log per repo whose checks
  changed (``kind: regression`` if anything went green→red, else
  ``kind: drift``), so the cockpit's Agents & changes pane and the HUD
  see it;
* a one-line summary as the last line of stdout, which
  ``run_routine.py`` lifts into the job summary.

Validation failures are findings, not errors: the sweep exits 0 with
``atlas 10/11`` in its report. It exits 1 only when it could not do its
job — jam-sdk failed to install, a repo would not clone, ``jam`` crashed.

State layout (``$JARVIS_ROUTINE_STATE``, set by run_routine.py)::

    jam-venv/          dedicated venv holding barelysmash-jam-sdk
    jam-venv/.jam-commit   the JAM commit the installed sdk came from
    repos/<name>/      shallow checkouts
    history/<name>/    previous reports
    report.json        this run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import venv
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from orchestrator import event_log
from orchestrator.routines import routine_state_dir

ROUTINE: Final[str] = "conformance-sweep"
DEFAULT_JAM_SOURCE: Final[str] = "https://github.com/barelysmash/JAM"
JAM_SDK_SUBDIR: Final[str] = "sdk"
HISTORY_KEEP: Final[int] = 30
REPO_SPEC: Final[re.Pattern[str]] = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)=(?P<url>[^@\s]+)(?:@(?P<branch>\S+))?$"
)


class SweepError(RuntimeError):
    """The sweep could not do its job (as opposed to: found something)."""


@dataclass(frozen=True, slots=True)
class RepoSpec:
    name: str
    url: str
    branch: str | None = None

    @classmethod
    def parse(cls, raw: str) -> RepoSpec:
        m = REPO_SPEC.match(raw.strip())
        if not m:
            raise argparse.ArgumentTypeError(
                f"--repo wants name=url[@branch], got '{raw}'"
            )
        return cls(m["name"], m["url"], m["branch"])


@dataclass(slots=True)
class RepoResult:
    name: str
    url: str
    branch: str | None
    commit: str = ""
    passed: bool | None = None
    total: int = 0
    passing: int = 0
    checks: dict[str, bool] = field(default_factory=dict)
    messages: dict[str, str] = field(default_factory=dict)
    regressed: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    previous_commit: str = ""
    error: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.regressed or self.fixed or self.added or self.removed)

    @property
    def score(self) -> str:
        if self.error:
            return "ERR"
        return f"{self.passing}/{self.total}"


# ─── Shell helpers ───────────────────────────────────────────


def sh(
    args: list[str], *, cwd: Path | None = None, check: bool = True, timeout: int = 600
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    proc = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        raise SweepError(
            f"{' '.join(args)} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:600]}"
        )
    return proc


def resolve_ref(source: str, ref: str) -> str:
    """The commit a branch or tag name points at on the remote, or ``ref``
    itself if it already looks like a commit."""

    if re.fullmatch(r"[0-9a-f]{7,40}", ref):
        return ref
    out = sh(
        [
            "git",
            "ls-remote",
            source,
            ref,
            f"refs/heads/{ref}",
            f"refs/tags/{ref}^{{}}",
            f"refs/tags/{ref}",
        ],
        timeout=120,
    ).stdout
    for line in out.splitlines():
        sha, _, _name = line.partition("\t")
        if sha:
            return sha
    raise SweepError(f"ref '{ref}' not found on {source}")


# ─── jam-sdk ─────────────────────────────────────────────────


def ensure_jam(state: Path, source: str, ref: str) -> tuple[Path, str]:
    """Install (or reuse) barelysmash-jam-sdk at ``ref`` in a private venv.

    Returns ``(path to the jam executable, JAM commit)``. Reinstalls only
    when the resolved commit differs from the one recorded in the venv, so a
    sweep pinned to ``main`` tracks JAM as it moves and a sweep pinned to a
    tag never touches the network for the sdk again.
    """

    commit = resolve_ref(source, ref)
    venv_dir = state / "jam-venv"
    marker = venv_dir / ".jam-commit"
    bindir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    jam = bindir / ("jam.exe" if os.name == "nt" else "jam")

    if jam.is_file() and marker.is_file() and marker.read_text().strip() == commit:
        return jam, commit

    if not (bindir / ("python.exe" if os.name == "nt" else "python")).exists():
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    sh(
        [str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        timeout=300,
    )
    sh(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--upgrade",
            f"git+{source}@{commit}#subdirectory={JAM_SDK_SUBDIR}",
        ],
        timeout=600,
    )
    if not jam.is_file():
        raise SweepError(f"jam-sdk installed but {jam} is missing")
    marker.write_text(commit + "\n")
    return jam, commit


# ─── Repositories ────────────────────────────────────────────


def sync_repo(repos_dir: Path, spec: RepoSpec) -> tuple[Path, str]:
    """Shallow checkout of ``spec`` at its branch tip. Returns (path, sha)."""

    path = repos_dir / spec.name
    branch = spec.branch
    if (path / ".git").is_dir():
        origin = sh(["git", "remote", "get-url", "origin"], cwd=path, check=False)
        if origin.stdout.strip() != spec.url:
            shutil.rmtree(path)
    if (path / ".git").is_dir():
        target = branch or "HEAD"
        sh(["git", "fetch", "--depth", "1", "--quiet", "origin", target], cwd=path)
        sh(["git", "reset", "--quiet", "--hard", "FETCH_HEAD"], cwd=path)
        sh(["git", "clean", "-fdq"], cwd=path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--quiet", "--depth", "1"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [spec.url, str(path)]
        sh(cmd)
    sha = sh(["git", "rev-parse", "--short", "HEAD"], cwd=path).stdout.strip()
    return path, sha


def validate_repo(jam: Path, repo: Path) -> dict[str, Any]:
    """``jam validate --json``; exit 1 means FAIL, which is still a report."""

    proc = sh([str(jam), "validate", str(repo), "--json"], check=False, timeout=300)
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        raise SweepError(
            f"jam validate exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:600]}"
        )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SweepError(f"jam validate produced invalid JSON: {exc}") from exc
    if not isinstance(report, dict) or "checks" not in report:
        raise SweepError("jam validate JSON has no 'checks'")
    return report


# ─── Diffing ─────────────────────────────────────────────────


def load_previous(history: Path, name: str) -> dict[str, Any] | None:
    latest = history / name / "latest.json"
    if not latest.is_file():
        return None
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def diff_against(result: RepoResult, previous: dict[str, Any] | None) -> None:
    if not previous:
        return
    old_checks = previous.get("checks")
    if not isinstance(old_checks, dict):
        return
    old: dict[str, bool] = {str(k): bool(v) for k, v in old_checks.items()}
    new = result.checks
    result.previous_commit = str(previous.get("commit", ""))
    result.regressed = sorted(k for k in new if k in old and old[k] and not new[k])
    result.fixed = sorted(k for k in new if k in old and not old[k] and new[k])
    result.added = sorted(k for k in new if k not in old)
    result.removed = sorted(k for k in old if k not in new)


def record(history: Path, result: RepoResult, stamp: str) -> None:
    folder = history / result.name
    folder.mkdir(parents=True, exist_ok=True)
    body = json.dumps(asdict(result), indent=2, sort_keys=True)
    (folder / "latest.json").write_text(body, encoding="utf-8")
    (folder / f"{stamp}.json").write_text(body, encoding="utf-8")
    dated = sorted(p for p in folder.glob("*.json") if p.name != "latest.json")
    for old in dated[:-HISTORY_KEEP] if len(dated) > HISTORY_KEEP else []:
        old.unlink(missing_ok=True)


# ─── Reporting ───────────────────────────────────────────────


def change_summary(result: RepoResult) -> str:
    bits: list[str] = []
    if result.regressed:
        bits.append("regressed: " + ", ".join(result.regressed))
    if result.fixed:
        bits.append("fixed: " + ", ".join(result.fixed))
    if result.added:
        bits.append("new checks: " + ", ".join(result.added))
    if result.removed:
        bits.append("dropped checks: " + ", ".join(result.removed))
    return "; ".join(bits)


def emit_changes(result: RepoResult) -> None:
    """One cockpit event per repo whose check set or outcomes moved."""

    if result.error:
        event_log.emit(
            ROUTINE,
            "agent",
            {
                "agent": ROUTINE,
                "kind": "error",
                "severity": "warning",
                "summary": f"{result.name}: could not validate — {result.error[:300]}",
                "data": {"repo": result.name, "url": result.url},
            },
        )
        return
    if not result.changed:
        return
    kind = "regression" if result.regressed else "drift"
    event_log.emit(
        ROUTINE,
        "agent",
        {
            "agent": ROUTINE,
            "kind": kind,
            "severity": "warning" if result.regressed else "notice",
            "summary": (
                f"{result.name} {result.score} @ {result.commit}: "
                f"{change_summary(result)}"
            ),
            "data": {
                "repo": result.name,
                "commit": result.commit,
                "previous_commit": result.previous_commit,
                "regressed": result.regressed,
                "fixed": result.fixed,
                "added": result.added,
                "removed": result.removed,
                "failing": sorted(k for k, ok in result.checks.items() if not ok),
            },
        },
    )


def print_result(result: RepoResult) -> None:
    head = f"{result.name:<16} {result.score:>6}  {result.commit or '-':<9}"
    if result.error:
        print(f"{head} ERROR {result.error}")
        return
    if result.changed:
        marker = "▲" if result.regressed else "△"
        print(f"{head} {marker} {change_summary(result)}")
    else:
        print(f"{head} =")
    for name, ok in result.checks.items():
        if not ok:
            print(f"    ✗ {name}: {result.messages.get(name, '')}")


def one_liner(results: list[RepoResult], jam_commit: str) -> str:
    parts = []
    for r in results:
        if r.error:
            parts.append(f"{r.name} ERR")
            continue
        flag = "=" if not r.changed else ("▲" if r.regressed else "△")
        parts.append(f"{r.name} {r.score} ({flag})")
    return " · ".join(parts) + f" · jam@{jam_commit[:7]}"


# ─── Main ────────────────────────────────────────────────────


def sweep(
    specs: list[RepoSpec], *, state: Path, jam_source: str, jam_ref: str
) -> tuple[list[RepoResult], str, bool]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    history = state / "history"
    repos_dir = state / "repos"

    jam, jam_commit = ensure_jam(state, jam_source, jam_ref)
    print(f"jam-sdk from {jam_source}@{jam_ref} ({jam_commit[:12]})")

    results: list[RepoResult] = []
    operational_error = False
    for spec in specs:
        result = RepoResult(name=spec.name, url=spec.url, branch=spec.branch)
        try:
            path, sha = sync_repo(repos_dir, spec)
            result.commit = sha
            report = validate_repo(jam, path)
            checks = report.get("checks") or []
            result.checks = {
                str(c["name"]): bool(c["passed"]) for c in checks if "name" in c
            }
            result.messages = {
                str(c["name"]): str(c.get("message", "")) for c in checks if "name" in c
            }
            result.total = len(result.checks)
            result.passing = sum(1 for ok in result.checks.values() if ok)
            result.passed = bool(report.get("passed", result.passing == result.total))
            diff_against(result, load_previous(history, spec.name))
            record(history, result, stamp)
        except (SweepError, subprocess.TimeoutExpired, OSError) as exc:
            result.error = str(exc)
            operational_error = True
        emit_changes(result)
        print_result(result)
        results.append(result)

    (state / "report.json").write_text(
        json.dumps(
            {
                "routine": ROUTINE,
                "ran_at": stamp,
                "jam": {"source": jam_source, "ref": jam_ref, "commit": jam_commit},
                "repos": [asdict(r) for r in results],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return results, jam_commit, operational_error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        action="append",
        type=RepoSpec.parse,
        default=[],
        metavar="NAME=URL[@BRANCH]",
    )
    parser.add_argument("--jam-source", default=DEFAULT_JAM_SOURCE)
    parser.add_argument("--jam-ref", default="main")
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="State dir (default: $JARVIS_ROUTINE_STATE, else the routine data dir).",
    )
    args = parser.parse_args(argv)

    if not args.repo:
        parser.error("at least one --repo is required")
    state_env = os.environ.get("JARVIS_ROUTINE_STATE")
    state = args.state or (Path(state_env) if state_env else routine_state_dir(ROUTINE))
    state.mkdir(parents=True, exist_ok=True)

    try:
        results, jam_commit, failed = sweep(
            list(args.repo),
            state=state,
            jam_source=args.jam_source,
            jam_ref=args.jam_ref,
        )
    except SweepError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"sweep aborted: {exc}"[:400])
        return 1

    print(one_liner(results, jam_commit))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
