"""Conformance sweep — routine #1 of the time-based loop.

    py scripts/routines/conformance_sweep.py \\
        --jam-ref main \\
        --repo atlas=https://github.com/barelysmash/atlas \\
        --repo jarvis-v2=https://github.com/barelysmash/jarvis-v2

For every ``--repo name=url[@branch]``: shallow-clone or fast-forward it
under the routine's state directory, run ``jam validate --json`` against it
with the pinned jam-sdk, and diff the result against the previous sweep.
The sweep *consumes* JAM; it never proposes changes to JAM itself.

It also watches JAM for releases (loop #2, ``jam_release.py``): the newest
``vX.Y.Z`` tag is compared with each repo's ``jam.yaml`` pin. With
``--propagate``, a repo that is behind gets a ``chore/jam-vX.Y.Z`` branch
prepared in its checkout — manifest pin bumped, vendored schemas refreshed,
VERSION note rewritten — plus a ``.patch`` under ``propagation/``. That is
where an unattended run stops. ``--push`` (needs write credentials and an
authenticated ``gh`` on the host) additionally pushes the branch and opens
a pull request; merging is always left to the repo's ``ship.sh``.

What comes out:

* ``report.json`` in the state dir — the full picture of this run;
* ``history/<repo>/latest.json`` (+ dated copies) — what the next run
  diffs against;
* one ``agent`` event in the JARVIS event log per repo whose checks
  changed (``kind: regression`` if anything went green→red, else
  ``kind: drift``), so the cockpit's Agents & changes pane and the HUD
  see it;
* ``kind: release`` once per new JAM tag, ``kind: behind`` when a repo's
  pin falls behind it, ``kind: propagation`` when a branch is prepared or
  a PR opened;
* a one-line summary as the last line of stdout, which
  ``run_routine.py`` lifts into the job summary.

Validation failures are findings, not errors: the sweep exits 0 with
``atlas 10/11`` in its report. It exits 1 only when it could not do its
job — jam-sdk failed to install, a repo would not clone, ``jam`` crashed.

State layout (``$JARVIS_ROUTINE_STATE``, set by run_routine.py)::

    jam-venv/          dedicated venv holding barelysmash-jam-sdk
    jam-venv/.jam-commit   the JAM commit the installed sdk came from
    jam/               tag-only JAM checkout (manifests + schemas by tag)
    jam-release.json   last seen release and per-repo pins
    repos/<name>/      shallow checkouts, HEAD detached at the tip;
                       prepared chore/jam-* branches live here
    propagation/       <repo>-jam-vX.Y.Z.patch for prepared branches
    history/<name>/    previous reports
    report.json        this run
"""

from __future__ import annotations

import argparse
import contextlib
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
from scripts.routines import jam_release

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
    pin: dict[str, Any] = field(default_factory=dict)
    propagation: dict[str, Any] = field(default_factory=dict)

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
        # Detach before resetting so a prepared chore/jam-* branch is never
        # the one that gets moved to the new tip.
        sh(["git", "checkout", "--quiet", "--detach"], cwd=path, check=False)
        sh(["git", "reset", "--quiet", "--hard", "FETCH_HEAD"], cwd=path)
        sh(["git", "clean", "-fdq"], cwd=path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--quiet", "--depth", "1"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [spec.url, str(path)]
        sh(cmd)
        sh(["git", "checkout", "--quiet", "--detach"], cwd=path)
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
    if result.pin:
        print(f"    {pin_summary(result)}")


def pin_summary(result: RepoResult) -> str:
    pin = result.pin
    if pin.get("error"):
        return f"pin: {pin['error']}"
    pinned, latest = pin.get("pinned"), pin.get("latest")
    if pinned is None:
        return "pin: jam.yaml has no manual_version"
    if not pin.get("behind"):
        return f"pin v{pinned} (latest)"
    text = f"pin v{pinned} → v{latest} BEHIND"
    prop = result.propagation
    if prop.get("error"):
        text += f"; propagation failed: {prop['error']}"
    elif prop.get("pr_url"):
        text += f"; PR {prop['pr_url']}"
    elif prop.get("commit"):
        patch_name = Path(prop["patch"]).name
        text += f"; {prop['branch']} ready ({prop['commit']}, patch {patch_name})"
    if pin.get("diverged"):
        text += "; diverged, left alone: " + ", ".join(pin["diverged"])
    return text


def one_liner(
    results: list[RepoResult], jam_commit: str, release_tag: str | None = None
) -> str:
    parts = []
    for r in results:
        if r.error:
            parts.append(f"{r.name} ERR")
            continue
        flag = "=" if not r.changed else ("▲" if r.regressed else "△")
        bit = f"{r.name} {r.score} ({flag})"
        if r.pin.get("behind"):
            prop = r.propagation
            if prop.get("pr_url"):
                bit += " PR open"
            elif prop.get("commit"):
                bit += " branch ready"
            else:
                bit += " behind"
        parts.append(bit)
    tail = f"jam {release_tag}" if release_tag else f"jam@{jam_commit[:7]}"
    return " · ".join(parts) + f" · {tail}"


# ─── JAM releases (loop #2) ──────────────────────────────────


def load_release_state(state: Path) -> dict[str, Any]:
    path = state / "jam-release.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_release_state(state: Path, data: dict[str, Any]) -> None:
    (state / "jam-release.json").write_text(
        json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
    )


def emit_release(release: jam_release.Release, previous_tag: str | None) -> None:
    event_log.emit(
        ROUTINE,
        "agent",
        {
            "agent": ROUTINE,
            "kind": "release",
            "severity": "notice",
            "summary": (
                f"JAM {release.tag} released"
                + (f" (was {previous_tag})" if previous_tag else "")
                + ": "
                + ", ".join(
                    f"{k} {v}" for k, v in sorted(release.schema_versions.items())
                )
            ),
            "data": {
                "tag": release.tag,
                "previous": previous_tag,
                "schemas": release.schema_versions,
            },
        },
    )


def emit_pin(result: RepoResult, release: jam_release.Release) -> None:
    pin, prop = result.pin, result.propagation
    if prop.get("error"):
        kind, severity = "propagation", "warning"
        summary = (
            f"{result.name}: could not prepare {release.tag} — {prop['error'][:300]}"
        )
    elif prop.get("pr_url"):
        kind, severity = "propagation", "notice"
        summary = f"{result.name}: PR for {release.tag} open — {prop['pr_url']}"
    elif prop.get("commit"):
        kind, severity = "propagation", "notice"
        summary = (
            f"{result.name}: {prop['branch']} ready ({prop['commit']}); "
            f"patch {Path(prop['patch']).name}"
        )
    elif pin.get("behind"):
        kind, severity = "behind", "notice"
        summary = f"{result.name}: pins JAM v{pin['pinned']}, latest is {release.tag}"
    else:
        return
    event_log.emit(
        ROUTINE,
        "agent",
        {
            "agent": ROUTINE,
            "kind": kind,
            "severity": severity,
            "summary": summary,
            "data": {"repo": result.name, "pin": pin, "propagation": prop},
        },
    )


def track_release(
    specs: list[RepoSpec],
    results: list[RepoResult],
    *,
    state: Path,
    jam_source: str,
    propagate: bool,
    push: bool,
) -> jam_release.Release | None:
    """Detect the newest JAM release, compare every synced repo's pin to it,
    and (optionally) prepare adoption branches. Events fire only when
    something is new: a tag, a pin falling behind, a branch prepared."""

    release = jam_release.latest_release(jam_source, state)
    if release is None:
        print("JAM has no release tags; skipping release tracking")
        return None
    jam_dir = state / "jam"
    patch_dir = state / "propagation"
    stored = load_release_state(state)
    previous_tag = stored.get("tag")
    print(
        f"JAM latest release {release.tag}"
        + (
            f" (was {previous_tag})"
            if previous_tag and previous_tag != release.tag
            else ""
        )
    )
    if previous_tag != release.tag:
        emit_release(release, previous_tag)

    seen_repos: dict[str, Any] = (
        dict(stored.get("repos", {})) if isinstance(stored.get("repos"), dict) else {}
    )
    for spec, result in zip(specs, results, strict=True):
        if result.error or not result.commit:
            continue
        repo_path = state / "repos" / spec.name
        try:
            status = jam_release.inspect_pin(spec.name, repo_path, release, jam_dir)
        except jam_release.ReleaseError as exc:
            result.pin = {"error": str(exc), "latest": release.version}
            continue
        result.pin = {
            "pinned": status.pinned,
            "latest": release.version,
            "behind": status.behind,
            "vendored": sorted(status.vendored),
            "diverged": status.diverged,
            "version_notes": status.version_notes,
            "error": status.error,
        }
        if status.behind and propagate and not status.error:
            pinned_release: jam_release.Release | None = None
            if status.pinned_tag:
                try:
                    pinned_release = jam_release.load_release(
                        jam_dir, status.pinned_tag
                    )
                except jam_release.ReleaseError:
                    pinned_release = None
            prop = jam_release.propagate(
                status,
                release,
                repo_path=repo_path,
                jam_dir=jam_dir,
                patch_dir=patch_dir,
                push=push,
                previous=pinned_release,
            )
            result.propagation = {
                "branch": prop.branch,
                "base": prop.base,
                "commit": prop.commit,
                "tree": prop.tree,
                "changed": prop.changed,
                "unchanged_schemas": prop.unchanged_schemas,
                "patch": prop.patch,
                "pushed": prop.pushed,
                "pr_url": prop.pr_url,
                "error": prop.error,
            }
        elif not status.behind:
            with contextlib.suppress(jam_release.ReleaseError):
                jam_release.cleanup_branches(repo_path, None, patch_dir, spec.name)

        # What counts as "new" for the cockpit: the pin pair, the prepared
        # content (tree, not commit — the branch is rebuilt nightly with a
        # fresh timestamp), a PR appearing, or an error changing.
        signature = {
            "pinned": status.pinned,
            "latest": release.version,
            "tree": result.propagation.get("tree", ""),
            "pr_url": result.propagation.get("pr_url", ""),
            "error": result.propagation.get("error", ""),
        }
        if seen_repos.get(spec.name) != signature:
            emit_pin(result, release)
        seen_repos[spec.name] = signature

    save_release_state(
        state,
        {"tag": release.tag, "schemas": release.schema_versions, "repos": seen_repos},
    )
    return release


# ─── Main ────────────────────────────────────────────────────


def sweep(
    specs: list[RepoSpec],
    *,
    state: Path,
    jam_source: str,
    jam_ref: str,
    propagate: bool = False,
    push: bool = False,
) -> tuple[list[RepoResult], str, bool, str | None]:
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
        results.append(result)

    release_tag: str | None = None
    try:
        release = track_release(
            specs,
            results,
            state=state,
            jam_source=jam_source,
            propagate=propagate,
            push=push,
        )
        release_tag = release.tag if release else None
    except (jam_release.ReleaseError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"release tracking failed: {exc}")
        event_log.emit(
            ROUTINE,
            "agent",
            {
                "agent": ROUTINE,
                "kind": "error",
                "severity": "warning",
                "summary": f"release tracking failed: {str(exc)[:300]}",
            },
        )
        operational_error = True

    for result in results:
        print_result(result)

    (state / "report.json").write_text(
        json.dumps(
            {
                "routine": ROUTINE,
                "ran_at": stamp,
                "jam": {
                    "source": jam_source,
                    "ref": jam_ref,
                    "commit": jam_commit,
                    "release": release_tag,
                },
                "repos": [asdict(r) for r in results],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return results, jam_commit, operational_error, release_tag


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
        "--propagate",
        action="store_true",
        help="Prepare a chore/jam-vX.Y.Z branch + patch in each repo behind JAM.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="With --propagate: push the branch and open a PR (needs gh auth).",
    )
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

    if args.push and not args.propagate:
        parser.error("--push requires --propagate")

    try:
        results, jam_commit, failed, release_tag = sweep(
            list(args.repo),
            state=state,
            jam_source=args.jam_source,
            jam_ref=args.jam_ref,
            propagate=args.propagate,
            push=args.push,
        )
    except SweepError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"sweep aborted: {exc}"[:400])
        return 1

    print(one_liner(results, jam_commit, release_tag))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
