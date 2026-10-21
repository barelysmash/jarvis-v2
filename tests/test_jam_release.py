"""JAM release detection and contract propagation (loop #2), fully offline.

Builds a local JAM with two tagged releases and three repos that pin it in
different ways, then drives ``jam_release`` directly and the whole
``conformance_sweep.sweep()`` with a fake ``jam`` binary. Everything is
``file://``; nothing touches GitHub or pip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "routines"))

import conformance_sweep  # noqa: E402
import jam_release  # noqa: E402

from orchestrator import event_log  # noqa: E402

SCHEMA_13 = {
    "observation": '{"$id": "observation", "version": "1.0.0"}\n',
    "insight": '{"$id": "insight", "version": "1.0.0"}\n',
    "decision": '{"$id": "decision", "version": "1.1.0"}\n',
}
SCHEMA_14 = {
    **SCHEMA_13,
    "decision": '{"$id": "decision", "version": "1.2.0", "new": true}\n',
}


def manifest(version: str, schemas: dict[str, str]) -> str:
    entries = []
    for name, text in schemas.items():
        entries.append(
            {
                "name": name,
                "version": json.loads(text)["version"],
                "path": f"schemas/{name}.schema.json",
            }
        )
    return json.dumps({"jam_version": version, "schemas": entries}, indent=2) + "\n"


def git(*args: str, cwd: Path) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.check_output(["git", *args], cwd=cwd, env=env, text=True)


def make_jam(root: Path, *, releases: tuple[str, ...] = ("v1.3.0", "v1.4.0")) -> str:
    """A JAM repo with one commit + tag per release. Returns its file:// URL."""

    path = root / "JAM"
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    (path / "schemas").mkdir()
    for tag in releases:
        schemas = SCHEMA_14 if tag == "v1.4.0" else SCHEMA_13
        for name, text in schemas.items():
            (path / "schemas" / f"{name}.schema.json").write_text(text)
        (path / "schemas" / "manifest.json").write_text(manifest(tag[1:], schemas))
        git("add", "-A", cwd=path)
        git("commit", "-q", "-m", f"JAM {tag}", cwd=path)
        git("tag", tag, cwd=path)
    if releases:
        git("tag", "not-a-release", cwd=path)
    return path.as_uri()


def make_repo(
    root: Path,
    name: str,
    *,
    pin: str,
    quoted: bool,
    vendor: dict[str, str] | None = None,
    diverge: str | None = None,
) -> str:
    path = root / name
    path.mkdir()
    git("init", "-q", "-b", "main", cwd=path)
    q = '"' if quoted else ""
    (path / "jam.yaml").write_text(
        textwrap.dedent(f"""\
        schema_version: "1.0"
        repository:
          name: {name}
        jam:
          manual_version: {q}{pin}{q}
          conformance_version: "1.0"
        """)
    )
    if vendor:
        contracts = path / "pkg" / "tests" / "contracts"
        contracts.mkdir(parents=True)
        for schema_name, text in vendor.items():
            if diverge == schema_name:
                text = text.replace("}", ', "local_edit": 1}', 1)
            (contracts / f"{schema_name}.schema.json").write_text(text)
        (contracts / "VERSION").write_text(
            "# Vendored JAM contract schemas.\n\n"
            f"jam v{pin}\n"
            + "".join(f"{n} {json.loads(t)['version']}\n" for n, t in vendor.items())
            + "\n# decision-state is not vendored.\n"
        )
    (path / "README.md").write_text(f"# {name}\n")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "init", cwd=path)
    return path.as_uri()


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._old = os.environ.get("JARVIS_DATA_DIR")
        os.environ["JARVIS_DATA_DIR"] = str(self.root / "data")
        self.addCleanup(self._restore)
        event_log._schema_ready_for = None
        self.state = self.root / "state"
        self.state.mkdir()
        self.jam_url = make_jam(self.root)

    def _restore(self) -> None:
        if self._old is None:
            os.environ.pop("JARVIS_DATA_DIR", None)
        else:
            os.environ["JARVIS_DATA_DIR"] = self._old

    def clone(self, url: str, name: str) -> Path:
        path = self.state / "repos" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "-q", url, str(path), cwd=self.root)
        git("checkout", "-q", "--detach", cwd=path)
        return path


class ReleaseDetectionTests(_Fixture):
    def test_latest_release_ignores_non_semver_tags(self) -> None:
        release = jam_release.latest_release(self.jam_url, self.state)
        assert release is not None
        self.assertEqual(release.tag, "v1.4.0")
        self.assertEqual(release.version, "1.4.0")
        self.assertEqual(release.schema_versions["decision"], "1.2.0")
        self.assertTrue((self.state / "jam" / ".git").is_dir())

    def test_fetch_tag_is_idempotent_and_reads_old_tags(self) -> None:
        jam_dir = jam_release.ensure_jam_checkout(self.state, self.jam_url)
        old = jam_release.load_release(jam_dir, "v1.3.0")
        self.assertEqual(old.schema_versions["decision"], "1.1.0")
        jam_release.fetch_tag(jam_dir, "v1.3.0")  # second call: no-op
        self.assertEqual(
            jam_release.read_at(jam_dir, "v1.3.0", "schemas/decision.schema.json"),
            SCHEMA_13["decision"],
        )

    def test_no_release_tags(self) -> None:
        url = make_jam(self.root / "empty", releases=())
        self.assertIsNone(jam_release.latest_release(url, self.state / "s2"))


class PinInspectionTests(_Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.release = jam_release.latest_release(self.jam_url, self.state)
        assert self.release is not None
        self.jam_dir = self.state / "jam"

    def test_vendored_and_diverged(self) -> None:
        url = make_repo(
            self.root,
            "atlas",
            pin="1.3.0",
            quoted=True,
            vendor=SCHEMA_13,
            diverge="insight",
        )
        path = self.clone(url, "atlas")
        status = jam_release.inspect_pin("atlas", path, self.release, self.jam_dir)
        self.assertEqual(status.pinned, "1.3.0")
        self.assertTrue(status.behind)
        self.assertEqual(
            sorted(status.vendored),
            [
                "pkg/tests/contracts/decision.schema.json",
                "pkg/tests/contracts/observation.schema.json",
            ],
        )
        self.assertEqual(status.diverged, ["pkg/tests/contracts/insight.schema.json"])
        self.assertEqual(status.version_notes, ["pkg/tests/contracts/VERSION"])

    def test_manifest_only_repo(self) -> None:
        url = make_repo(self.root, "jarvis", pin="1.4.0", quoted=False)
        path = self.clone(url, "jarvis")
        status = jam_release.inspect_pin("jarvis", path, self.release, self.jam_dir)
        self.assertFalse(status.behind)
        self.assertEqual(status.vendored, {})

    def test_no_pin(self) -> None:
        path = self.root / "bare"
        path.mkdir()
        status = jam_release.inspect_pin("bare", path, self.release, self.jam_dir)
        self.assertIsNone(status.pinned)
        self.assertFalse(status.behind)


class PropagationTests(_Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.release = jam_release.latest_release(self.jam_url, self.state)
        assert self.release is not None
        self.jam_dir = self.state / "jam"
        self.patch_dir = self.state / "propagation"

    def _prepare(self, name: str, **kw: object) -> tuple[Path, jam_release.Propagation]:
        url = make_repo(self.root, name, **kw)  # type: ignore[arg-type]
        path = self.clone(url, name)
        status = jam_release.inspect_pin(name, path, self.release, self.jam_dir)
        previous = jam_release.load_release(self.jam_dir, "v1.3.0")
        prop = jam_release.propagate(
            status,
            self.release,
            repo_path=path,
            jam_dir=self.jam_dir,
            patch_dir=self.patch_dir,
            previous=previous,
        )
        return path, prop

    def test_vendoring_repo_gets_branch_patch_and_clean_tree(self) -> None:
        path, prop = self._prepare("atlas", pin="1.3.0", quoted=True, vendor=SCHEMA_13)
        self.assertTrue(prop.prepared, prop.error)
        self.assertEqual(prop.branch, "chore/jam-v1.4.0")
        # jam.yaml bumped, quoting preserved
        on_branch = git("show", f"{prop.branch}:jam.yaml", cwd=path)
        self.assertIn('manual_version: "1.4.0"', on_branch)
        # decision refreshed, observation/insight unchanged
        self.assertEqual(
            git(
                "show",
                f"{prop.branch}:pkg/tests/contracts/decision.schema.json",
                cwd=path,
            ),
            SCHEMA_14["decision"],
        )
        self.assertEqual(
            sorted(prop.changed),
            [
                "jam.yaml",
                "pkg/tests/contracts/VERSION",
                "pkg/tests/contracts/decision.schema.json",
            ],
        )
        self.assertEqual(
            sorted(prop.unchanged_schemas),
            [
                "pkg/tests/contracts/insight.schema.json",
                "pkg/tests/contracts/observation.schema.json",
            ],
        )
        note = git("show", f"{prop.branch}:pkg/tests/contracts/VERSION", cwd=path)
        self.assertIn("jam v1.4.0\n", note)
        self.assertIn("decision 1.2.0\n", note)
        self.assertIn("observation 1.0.0\n", note)
        self.assertIn("# decision-state is not vendored.", note)
        # commit message explains the bump
        msg = git("log", "-1", "--format=%B", prop.branch, cwd=path)
        self.assertIn("chore: adopt JAM v1.4.0", msg)
        self.assertIn("decision 1.1.0 -> 1.2.0", msg)
        self.assertIn("CI is the gate", msg)
        # patch written, HEAD detached back at base, tree clean
        self.assertTrue(Path(prop.patch).is_file())
        self.assertIn(
            "Subject: [PATCH] chore: adopt JAM v1.4.0", Path(prop.patch).read_text()
        )
        self.assertEqual(git("rev-parse", "HEAD", cwd=path).strip(), prop.base)
        self.assertEqual(git("status", "--porcelain", cwd=path), "")
        detached = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"], cwd=path, capture_output=True
        )
        self.assertNotEqual(detached.returncode, 0, "HEAD should be detached")

    def test_manifest_only_repo_bumps_unquoted_pin(self) -> None:
        path, prop = self._prepare("jarvis", pin="1.3.0", quoted=False)
        self.assertTrue(prop.prepared, prop.error)
        self.assertEqual(prop.changed, ["jam.yaml"])
        self.assertIn(
            "manual_version: 1.4.0\n", git("show", f"{prop.branch}:jam.yaml", cwd=path)
        )

    def test_diverged_schema_is_left_alone(self) -> None:
        path, prop = self._prepare(
            "atlas", pin="1.3.0", quoted=True, vendor=SCHEMA_13, diverge="decision"
        )
        self.assertTrue(prop.prepared, prop.error)
        self.assertNotIn("pkg/tests/contracts/decision.schema.json", prop.changed)
        self.assertIn(
            '"local_edit": 1',
            git(
                "show",
                f"{prop.branch}:pkg/tests/contracts/decision.schema.json",
                cwd=path,
            ),
        )
        self.assertIn(
            "Left alone", git("log", "-1", "--format=%B", prop.branch, cwd=path)
        )

    def test_rerun_is_idempotent_and_cleanup_removes_when_current(self) -> None:
        path, _first = self._prepare(
            "atlas", pin="1.3.0", quoted=True, vendor=SCHEMA_13
        )
        status = jam_release.inspect_pin("atlas", path, self.release, self.jam_dir)
        second = jam_release.propagate(
            status,
            self.release,
            repo_path=path,
            jam_dir=self.jam_dir,
            patch_dir=self.patch_dir,
        )
        self.assertTrue(second.prepared)
        self.assertEqual(
            git(
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads/chore/",
                cwd=path,
            ).split(),
            ["chore/jam-v1.4.0"],
        )
        self.assertEqual(len(list(self.patch_dir.glob("atlas-jam-*.patch"))), 1)
        jam_release.cleanup_branches(path, None, self.patch_dir, "atlas")
        self.assertEqual(git("for-each-ref", "refs/heads/chore/", cwd=path), "")
        self.assertEqual(list(self.patch_dir.glob("atlas-jam-*.patch")), [])


class SweepIntegrationTests(_Fixture):
    """The whole sweep, with jam-sdk replaced by a script that reports PASS."""

    def setUp(self) -> None:
        super().setUp()
        # A stand-in for the jam-sdk CLI. Lives in its own directory under a
        # name that cannot collide with the JAM fixture repo (Windows paths
        # are case-insensitive, so "jam" and "JAM" would), and is run through
        # the interpreter rather than as an executable so it works where bash
        # is not on PATH and exec bits do not exist.
        fake_dir = self.root / "fake-sdk"
        fake_dir.mkdir()
        fake = fake_dir / "fake_jam.py"
        fake.write_text(
            "import json, sys\n"
            "print(json.dumps({'repository': sys.argv[2], 'passed': True, "
            "'checks': [{'name': 'jam-manifest', 'passed': True, "
            "'message': 'ok'}]}))\n"
        )
        self._orig_ensure = conformance_sweep.ensure_jam
        self._orig_validate = conformance_sweep.validate_repo

        def fake_validate(jam: Path, repo: Path) -> dict[str, object]:
            proc = subprocess.run(
                [sys.executable, str(fake), "validate", str(repo), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(proc.stdout)  # type: ignore[no-any-return]

        conformance_sweep.ensure_jam = lambda state, source, ref: (fake, "deadbeefcafe")  # type: ignore[assignment]
        conformance_sweep.validate_repo = fake_validate  # type: ignore[assignment]
        self.addCleanup(setattr, conformance_sweep, "ensure_jam", self._orig_ensure)
        self.addCleanup(
            setattr, conformance_sweep, "validate_repo", self._orig_validate
        )

    def _events(self, kind: str) -> list[dict[str, object]]:
        return [
            e["payload"]
            for e in event_log.fetch_tail(100)
            if e["type"] == "agent" and e["payload"].get("kind") == kind
        ]

    def test_two_sweeps_detect_release_prepare_once_and_stay_quiet(self) -> None:
        atlas = make_repo(
            self.root, "atlas", pin="1.3.0", quoted=True, vendor=SCHEMA_13
        )
        jarvis = make_repo(self.root, "jarvis", pin="1.4.0", quoted=False)
        specs = [
            conformance_sweep.RepoSpec("atlas", atlas),
            conformance_sweep.RepoSpec("jarvis", jarvis),
        ]
        results, _, failed, tag = conformance_sweep.sweep(
            specs,
            state=self.state,
            jam_source=self.jam_url,
            jam_ref="main",
            propagate=True,
        )
        self.assertFalse(failed)
        self.assertEqual(tag, "v1.4.0")
        by_name = {r.name: r for r in results}
        self.assertTrue(by_name["atlas"].pin["behind"])
        self.assertFalse(by_name["jarvis"].pin["behind"])
        self.assertEqual(by_name["atlas"].propagation["branch"], "chore/jam-v1.4.0")
        self.assertTrue(Path(by_name["atlas"].propagation["patch"]).is_file())
        self.assertEqual(len(self._events("release")), 1)
        self.assertEqual(len(self._events("propagation")), 1)
        self.assertEqual(self._events("behind"), [])
        line = conformance_sweep.one_liner(results, "deadbeefcafe", tag)
        self.assertEqual(
            line, "atlas 1/1 (=) branch ready · jarvis 1/1 (=) · jam v1.4.0"
        )
        state = json.loads((self.state / "jam-release.json").read_text())
        self.assertEqual(state["tag"], "v1.4.0")
        self.assertEqual(state["repos"]["atlas"]["pinned"], "1.3.0")

        # Second night: nothing new — same branch rebuilt, no new events.
        _results2, _, failed2, _ = conformance_sweep.sweep(
            specs,
            state=self.state,
            jam_source=self.jam_url,
            jam_ref="main",
            propagate=True,
        )
        self.assertFalse(failed2)
        self.assertEqual(len(self._events("release")), 1)
        self.assertEqual(len(self._events("propagation")), 1)
        atlas_path = self.state / "repos" / "atlas"
        self.assertEqual(git("status", "--porcelain", cwd=atlas_path), "")
        self.assertEqual(
            git(
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads/chore/",
                cwd=atlas_path,
            ).split(),
            ["chore/jam-v1.4.0"],
        )

    def test_without_propagate_reports_behind_once(self) -> None:
        atlas = make_repo(
            self.root, "atlas", pin="1.3.0", quoted=True, vendor=SCHEMA_13
        )
        specs = [conformance_sweep.RepoSpec("atlas", atlas)]
        for _ in range(2):
            results, _, failed, _ = conformance_sweep.sweep(
                specs, state=self.state, jam_source=self.jam_url, jam_ref="main"
            )
            self.assertFalse(failed)
        self.assertEqual(len(self._events("behind")), 1)
        self.assertEqual(self._events("propagation"), [])
        self.assertEqual(results[0].propagation, {})
        self.assertIn("behind", conformance_sweep.one_liner(results, "x", "v1.4.0"))


if __name__ == "__main__":
    unittest.main()
