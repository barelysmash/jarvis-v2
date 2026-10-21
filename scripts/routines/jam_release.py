"""JAM release detection and contract propagation — loop #2.

Used by ``conformance_sweep.py``. Nothing here is a routine on its own.

A JAM release is a ``vX.Y.Z`` tag on the JAM repository; ``schemas/manifest.json``
at that tag says which contract versions it ships. A conforming repository
pins the release in ``jam.yaml`` (``jam.manual_version``) and may vendor
schema files (``<name>.schema.json``) beside a ``VERSION`` note that records
where they came from — Atlas does; JARVIS only carries the manifest pin.

Three steps, each pure enough to test offline against ``file://`` repos:

* :func:`latest_release` — the newest semver tag and its manifest, read
  from a tag-only checkout under the routine's state dir;
* :func:`inspect_pin` — what a repository pins, which of its schema files
  are byte-identical to JAM's at that pin (so safe to refresh) and which
  have diverged (left alone, reported);
* :func:`propagate` — on a branch ``chore/jam-vX.Y.Z`` off the repo's tip:
  bump the manifest pin, refresh vendored schemas from the new tag, rewrite
  the VERSION note, commit, and write a ``.patch``. Then, only with
  ``push=True``: push the branch and open a pull request. Never merge.

The stop-before-push default is the loop's hand-off boundary: guildenstern
has read-only GitHub access, so a prepared branch plus a patch is as far as
an unattended run can go without someone deciding to give it write
credentials. Merging stays with each repository's ``ship.sh`` either way.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

SEMVER_TAG: Final[re.Pattern[str]] = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
MANIFEST_PATH: Final[str] = "schemas/manifest.json"
SCHEMA_FILE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<name>[a-z][a-z0-9-]*)\.schema\.json$"
)
PIN_LINE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<lead>\s*manual_version:\s*)(?P<q>[\"']?)(?P<ver>\d+\.\d+\.\d+)(?P=q)",
    re.MULTILINE,
)
BRANCH_PREFIX: Final[str] = "chore/jam-"
GIT_IDENTITY: Final[dict[str, str]] = {
    "GIT_AUTHOR_NAME": "JARVIS conformance-sweep",
    "GIT_AUTHOR_EMAIL": "jarvis@barelysmash.local",
    "GIT_COMMITTER_NAME": "JARVIS conformance-sweep",
    "GIT_COMMITTER_EMAIL": "jarvis@barelysmash.local",
}
IGNORED_DIRS: Final[frozenset[str]] = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
)


class ReleaseError(RuntimeError):
    """Release detection or propagation could not do its job."""


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
        raise ReleaseError(
            f"git {' '.join(args)} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:600]}"
        )
    return proc


def semver_key(tag: str) -> tuple[int, int, int] | None:
    m = SEMVER_TAG.match(tag)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


# ─── JAM releases ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Release:
    tag: str
    version: str
    schema_versions: dict[str, str]
    manifest: dict[str, Any]

    @property
    def schema_names(self) -> frozenset[str]:
        return frozenset(self.schema_versions)


def ensure_jam_checkout(state: Path, source: str) -> Path:
    """A tag-only JAM checkout at ``state/jam`` (no working tree needed)."""

    jam_dir = state / "jam"
    if not (jam_dir / ".git").is_dir():
        jam_dir.mkdir(parents=True, exist_ok=True)
        git(["init", "-q"], cwd=jam_dir)
        git(["remote", "add", "origin", source], cwd=jam_dir)
    else:
        current = git(["remote", "get-url", "origin"], cwd=jam_dir, check=False)
        if current.stdout.strip() != source:
            git(["remote", "set-url", "origin", source], cwd=jam_dir)
    return jam_dir


def fetch_tag(jam_dir: Path, tag: str) -> None:
    """Make ``tag`` readable locally; a no-op once it has been fetched."""

    if (
        git(
            ["rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"],
            cwd=jam_dir,
            check=False,
        ).returncode
        == 0
    ):
        return
    git(
        ["fetch", "--quiet", "--depth", "1", "origin", "tag", tag, "--no-tags"],
        cwd=jam_dir,
    )


def read_at(jam_dir: Path, tag: str, path: str) -> str | None:
    fetch_tag(jam_dir, tag)
    proc = git(["show", f"refs/tags/{tag}:{path}"], cwd=jam_dir, check=False)
    return proc.stdout if proc.returncode == 0 else None


def list_release_tags(source: str, jam_dir: Path) -> list[str]:
    out = git(
        ["ls-remote", "--tags", "--refs", source], cwd=jam_dir, timeout=120
    ).stdout
    tags = []
    for line in out.splitlines():
        _sha, _, ref = line.partition("\t")
        tag = ref.removeprefix("refs/tags/")
        if semver_key(tag):
            tags.append(tag)
    return sorted(tags, key=lambda t: semver_key(t) or (0, 0, 0))


def load_release(jam_dir: Path, tag: str) -> Release:
    text = read_at(jam_dir, tag, MANIFEST_PATH)
    if text is None:
        raise ReleaseError(f"JAM {tag} has no {MANIFEST_PATH}")
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReleaseError(
            f"JAM {tag} {MANIFEST_PATH} is not valid JSON: {exc}"
        ) from exc
    versions: dict[str, str] = {}
    for entry in manifest.get("schemas", []):
        if isinstance(entry, dict) and "name" in entry and "version" in entry:
            versions[str(entry["name"])] = str(entry["version"])
    if not versions:
        raise ReleaseError(f"JAM {tag} manifest lists no schemas")
    return Release(
        tag=tag,
        version=str(manifest.get("jam_version", tag.lstrip("v"))),
        schema_versions=versions,
        manifest=manifest,
    )


def latest_release(source: str, state: Path) -> Release | None:
    """Newest ``vX.Y.Z`` on the remote, or None when JAM has no release tags."""

    jam_dir = ensure_jam_checkout(state, source)
    tags = list_release_tags(source, jam_dir)
    if not tags:
        return None
    return load_release(jam_dir, tags[-1])


# ─── Repository pins ─────────────────────────────────────────


@dataclass(slots=True)
class PinStatus:
    repo: str
    pinned: str | None  # "1.3.0"; None when jam.yaml has no pin
    latest: str  # "1.4.0"
    vendored: dict[str, str] = field(default_factory=dict)  # rel path -> schema name
    diverged: list[str] = field(default_factory=list)  # rel paths left alone
    version_notes: list[str] = field(default_factory=list)  # rel paths
    error: str = ""

    @property
    def behind(self) -> bool:
        if self.pinned is None:
            return False
        return (semver_key(f"v{self.pinned}") or (0, 0, 0)) < (
            semver_key(f"v{self.latest}") or (0, 0, 0)
        )

    @property
    def pinned_tag(self) -> str | None:
        return f"v{self.pinned}" if self.pinned else None


def read_pin(repo_path: Path) -> str | None:
    manifest = repo_path / "jam.yaml"
    if not manifest.is_file():
        return None
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    jam = data.get("jam") if isinstance(data, dict) else None
    if not isinstance(jam, dict):
        return None
    pin = jam.get("manual_version")
    return str(pin) if pin is not None else None


def _schema_files(repo_path: Path) -> list[Path]:
    found = []
    for path in repo_path.rglob("*.schema.json"):
        if any(part in IGNORED_DIRS for part in path.relative_to(repo_path).parts):
            continue
        if SCHEMA_FILE.match(path.name):
            found.append(path)
    return sorted(found)


def inspect_pin(
    repo_name: str, repo_path: Path, latest: Release, jam_dir: Path
) -> PinStatus:
    """What the repo pins, and which schema files are safe to refresh.

    A schema file is *vendored* when its content is byte-identical to JAM's
    at the repo's pinned tag — that is the only way to know it was copied
    rather than written. Anything else with a JAM schema's name is reported
    as diverged and never touched.
    """

    status = PinStatus(
        repo=repo_name, pinned=read_pin(repo_path), latest=latest.version
    )
    pinned_tag = status.pinned_tag
    if pinned_tag is None:
        return status
    try:
        fetch_tag(jam_dir, pinned_tag)
    except ReleaseError as exc:
        status.error = f"pinned JAM {pinned_tag} is not a release tag: {exc}"
        return status

    for path in _schema_files(repo_path):
        m = SCHEMA_FILE.match(path.name)
        name = m["name"] if m else ""
        if name not in latest.schema_names:
            continue
        rel = path.relative_to(repo_path).as_posix()
        at_pin = read_at(jam_dir, pinned_tag, f"schemas/{path.name}")
        if at_pin is not None and at_pin == path.read_text(encoding="utf-8"):
            status.vendored[rel] = name
            note = path.parent / "VERSION"
            if note.is_file() and re.search(
                r"^jam v\d", note.read_text(encoding="utf-8"), re.M
            ):
                rel_note = note.relative_to(repo_path).as_posix()
                if rel_note not in status.version_notes:
                    status.version_notes.append(rel_note)
        else:
            status.diverged.append(rel)
    return status


# ─── Propagation ─────────────────────────────────────────────


@dataclass(slots=True)
class Propagation:
    repo: str
    tag: str
    branch: str
    base: str
    changed: list[str] = field(default_factory=list)
    unchanged_schemas: list[str] = field(default_factory=list)
    commit: str = ""
    tree: str = ""  # content identity: same files -> same tree, any timestamp
    patch: str = ""
    pushed: bool = False
    pr_url: str = ""
    error: str = ""

    @property
    def prepared(self) -> bool:
        return bool(self.commit) and not self.error


def branch_for(tag: str) -> str:
    return f"{BRANCH_PREFIX}{tag}"


def bump_manifest_pin(text: str, new_version: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return f"{m['lead']}{m['q']}{new_version}{m['q']}"

    updated, n = PIN_LINE.subn(repl, text, count=1)
    if n == 0:
        raise ReleaseError("jam.yaml has no 'manual_version:' line to bump")
    return updated


def rewrite_version_note(text: str, release: Release) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if re.match(r"^jam v\d", stripped):
            line = re.sub(r"v\S+", release.tag, line, count=1)
        else:
            m = re.match(
                r"^(?P<name>[a-z][a-z0-9-]*) (?P<ver>\d+\.\d+\.\d+)\s*$", stripped
            )
            if m and m["name"] in release.schema_versions:
                line = line.replace(m["ver"], release.schema_versions[m["name"]], 1)
        lines.append(line)
    return "".join(lines)


def _commit_message(
    status: PinStatus, release: Release, previous: Release | None
) -> str:
    lines = [
        f"chore: adopt JAM {release.tag}",
        "",
        f"jam.yaml: manual_version {status.pinned} -> {release.version}",
    ]
    if status.vendored:
        lines.append("")
        lines.append(f"Vendored schemas refreshed from JAM {release.tag}:")
        for rel, name in sorted(status.vendored.items()):
            old = previous.schema_versions.get(name, "?") if previous else "?"
            new = release.schema_versions.get(name, "?")
            arrow = f"{old} -> {new}" if old != new else f"{new} (unchanged)"
            lines.append(f"  {rel}: {name} {arrow}")
    if status.diverged:
        lines.append("")
        lines.append(
            "Left alone (differs from JAM at the previous pin; needs a human):"
        )
        lines += [f"  {rel}" for rel in status.diverged]
    lines += [
        "",
        "Prepared by the JARVIS conformance-sweep routine. CI is the gate: if the",
        "contract tests fail, code that declares schema versions (SCHEMA_VERSIONS",
        "or similar) needs the matching bump before this merges.",
    ]
    return "\n".join(lines) + "\n"


def cleanup_branches(
    repo_path: Path, keep: str | None, patch_dir: Path, repo_name: str
) -> None:
    """Delete prepared ``chore/jam-*`` branches (and patches) other than ``keep``."""

    out = git(
        ["for-each-ref", "--format=%(refname:short)", f"refs/heads/{BRANCH_PREFIX}*"],
        cwd=repo_path,
    ).stdout
    for branch in out.split():
        if branch != keep:
            git(["branch", "-D", "-q", branch], cwd=repo_path, check=False)
    for patch in patch_dir.glob(f"{repo_name}-jam-*.patch"):
        if keep is None or not patch.name.endswith(
            f"{keep.removeprefix(BRANCH_PREFIX)}.patch"
        ):
            patch.unlink(missing_ok=True)


def propagate(
    status: PinStatus,
    release: Release,
    *,
    repo_path: Path,
    jam_dir: Path,
    patch_dir: Path,
    push: bool = False,
    previous: Release | None = None,
) -> Propagation:
    """Prepare (and optionally push) the adoption branch. HEAD ends detached
    where it started; the working tree is left clean."""

    base = git(["rev-parse", "HEAD"], cwd=repo_path).stdout.strip()
    branch = branch_for(release.tag)
    prop = Propagation(repo=status.repo, tag=release.tag, branch=branch, base=base)
    patch_dir.mkdir(parents=True, exist_ok=True)

    try:
        git(["checkout", "-q", "-B", branch, base], cwd=repo_path)

        manifest = repo_path / "jam.yaml"
        manifest.write_text(
            bump_manifest_pin(manifest.read_text(encoding="utf-8"), release.version),
            encoding="utf-8",
            newline="\n",
        )
        prop.changed.append("jam.yaml")

        for rel, name in sorted(status.vendored.items()):
            new_text = read_at(jam_dir, release.tag, f"schemas/{name}.schema.json")
            if new_text is None:
                prop.unchanged_schemas.append(
                    f"{rel} (JAM {release.tag} no longer ships {name})"
                )
                continue
            target = repo_path / rel
            if target.read_text(encoding="utf-8") == new_text:
                prop.unchanged_schemas.append(rel)
                continue
            target.write_text(new_text, encoding="utf-8", newline="\n")
            prop.changed.append(rel)

        for rel in status.version_notes:
            note = repo_path / rel
            updated = rewrite_version_note(note.read_text(encoding="utf-8"), release)
            if updated != note.read_text(encoding="utf-8"):
                note.write_text(updated, encoding="utf-8", newline="\n")
                prop.changed.append(rel)

        git(["add", "--", *prop.changed], cwd=repo_path)
        message = _commit_message(status, release, previous)
        message_file = patch_dir / f".{status.repo}-commit-msg"
        message_file.write_text(message, encoding="utf-8", newline="\n")
        try:
            git(["commit", "-q", "-F", str(message_file)], cwd=repo_path)
        finally:
            message_file.unlink(missing_ok=True)
        prop.commit = git(
            ["rev-parse", "--short", "HEAD"], cwd=repo_path
        ).stdout.strip()
        prop.tree = git(["rev-parse", "HEAD^{tree}"], cwd=repo_path).stdout.strip()

        patch = patch_dir / f"{status.repo}-jam-{release.tag}.patch"
        patch.write_text(
            git(["format-patch", "-1", "--stdout", "HEAD"], cwd=repo_path).stdout,
            encoding="utf-8",
            newline="\n",
        )
        prop.patch = str(patch)

        if push:
            _push_and_open_pr(prop, repo_path, message)
    except ReleaseError as exc:
        prop.error = str(exc)
    finally:
        git(["checkout", "-q", "--detach", base], cwd=repo_path, check=False)
        git(["clean", "-fdq"], cwd=repo_path, check=False)

    cleanup_branches(repo_path, branch, patch_dir, status.repo)
    return prop


def _push_and_open_pr(prop: Propagation, repo_path: Path, message: str) -> None:
    """Push the branch and make sure a PR exists for it. Requires an
    authenticated ``gh`` and a pushable origin; raises ReleaseError otherwise.
    Merging is deliberately not done here — that is ship.sh's job."""

    git(
        ["push", "--quiet", "--force-with-lease", "-u", "origin", prop.branch],
        cwd=repo_path,
        timeout=300,
    )
    prop.pushed = True
    view = subprocess.run(
        ["gh", "pr", "view", prop.branch, "--json", "url", "--jq", ".url"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if view.returncode == 0 and view.stdout.strip():
        prop.pr_url = view.stdout.strip()
        return
    title, _, body = message.partition("\n\n")
    create = subprocess.run(
        ["gh", "pr", "create", "--head", prop.branch, "--title", title, "--body", body],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if create.returncode != 0:
        raise ReleaseError(
            f"gh pr create failed: {(create.stderr or create.stdout).strip()[:400]}"
        )
    prop.pr_url = (
        create.stdout.strip().splitlines()[-1] if create.stdout.strip() else ""
    )
