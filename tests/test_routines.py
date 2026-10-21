"""Routine registry, unit rendering, runner and conformance-sweep diffing.

Pins the three contracts the time-based loop rests on:

* ``config/routines.yaml`` loads, and a malformed registry fails loudly
  rather than scheduling nothing;
* the committed ``deploy/systemd/jarvis-routine-*`` units are exactly what
  the registry renders (the same check CI runs);
* ``run_routine.py`` brackets a script in ``event_log.job`` with the
  script's last stdout line as the summary, and ``conformance_sweep``'s
  diff classifies regressions / fixes / added / removed checks.

Nothing here touches systemd, the network, or a real ``jam`` binary.
"""

from __future__ import annotations

import argparse
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
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "routines"))

import conformance_sweep  # noqa: E402
import gen_routine_units  # noqa: E402
import run_routine  # noqa: E402

from orchestrator import event_log  # noqa: E402
from orchestrator.routines import (  # noqa: E402
    Routine,
    RoutineConfigError,
    load_routines,
    render_service,
    render_timer,
    render_units,
)


class _TempData(unittest.TestCase):
    """Point JARVIS_DATA_DIR at a temp dir so event_log writes stay local."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old = os.environ.get("JARVIS_DATA_DIR")
        os.environ["JARVIS_DATA_DIR"] = self.tmp.name
        self.addCleanup(self._restore)
        event_log._schema_ready_for = None

    def _restore(self) -> None:
        if self._old is None:
            os.environ.pop("JARVIS_DATA_DIR", None)
        else:
            os.environ["JARVIS_DATA_DIR"] = self._old

    def write_registry(self, body: str) -> Path:
        path = Path(self.tmp.name) / "routines.yaml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path


class RegistryTests(_TempData):
    def test_committed_registry_loads(self) -> None:
        routines = load_routines(REPO / "config" / "routines.yaml")
        self.assertIn("conformance-sweep", routines)
        sweep = routines["conformance-sweep"]
        self.assertEqual(sweep.kind, "shell")
        self.assertTrue((REPO / sweep.script).is_file(), sweep.script)
        self.assertIn("--repo", sweep.args)

    def test_committed_units_match_registry(self) -> None:
        _, write, _, delete = gen_routine_units.plan(
            REPO / "config" / "routines.yaml", REPO / "deploy" / "systemd"
        )
        self.assertEqual(write, [], "regenerate: py scripts/gen_routine_units.py")
        self.assertEqual(delete, [], "orphan unit(s) in deploy/systemd")

    def test_defaults_and_order(self) -> None:
        path = self.write_registry("""
            routines:
              zeta:
                description: z
                schedule: "daily"
                kind: shell
                script: scripts/z.py
              alpha:
                description: >
                  multi
                  line
                schedule: "*-*-* 03:00:00"
                kind: shell
                script: scripts/a.py
                args: [--n, 3]
                timeout_minutes: 5
                jitter_seconds: 30
                enabled: false
            """)
        routines = load_routines(path)
        self.assertEqual(list(routines), ["zeta", "alpha"])
        z = routines["zeta"]
        self.assertEqual(
            (z.timeout_minutes, z.jitter_seconds, z.enabled), (30, 0, True)
        )
        a = routines["alpha"]
        self.assertEqual(a.description, "multi line")
        self.assertEqual(a.args, ("--n", "3"))
        self.assertFalse(a.enabled)

    def test_rejections(self) -> None:
        cases = {
            "goal without its fields": """
                routines:
                  x: {description: d, schedule: s, kind: goal, script: s.py}
                """,
            "unknown kind": """
                routines:
                  x: {description: d, schedule: s, kind: cron, script: s.py}
                """,
            "bad name": """
                routines:
                  Bad_Name: {description: d, schedule: s, kind: shell, script: s.py}
                """,
            "absolute script": """
                routines:
                  x: {description: d, schedule: s, kind: shell, script: /etc/x.py}
                """,
            "escaping script": """
                routines:
                  x: {description: d, schedule: s, kind: shell, script: ../x.py}
                """,
            "missing schedule": """
                routines:
                  x: {description: d, kind: shell, script: s.py}
                """,
            "bool timeout": """
                routines:
                  x: {description: d, schedule: s, kind: shell, script: s.py,
                      timeout_minutes: true}
                """,
            "no routines key": """
                agents: {}
                """,
        }
        for label, body in cases.items():
            with self.subTest(label), self.assertRaises(RoutineConfigError):
                load_routines(self.write_registry(body))

    def test_missing_file(self) -> None:
        with self.assertRaises(RoutineConfigError):
            load_routines(Path(self.tmp.name) / "nope.yaml")


class RenderTests(unittest.TestCase):
    def routine(self, **over: object) -> Routine:
        base: dict[str, object] = {
            "name": "sweep",
            "description": "Sweep it.",
            "schedule": "Mon..Fri 05:30:00",
            "kind": "shell",
            "script": "scripts/routines/sweep.py",
        }
        base.update(over)
        return Routine(**base)  # type: ignore[arg-type]

    def test_service_shape(self) -> None:
        text = render_service(self.routine(timeout_minutes=10))
        self.assertIn("Type=oneshot", text)
        self.assertIn("WorkingDirectory=%h/jarvis", text)
        self.assertIn("EnvironmentFile=%h/jarvis-data/env/jarvis.env", text)
        self.assertIn("Environment=JARVIS_PROC=routine", text)
        self.assertIn(
            "ExecStart=%h/jarvis-venv/bin/python scripts/run_routine.py sweep", text
        )
        self.assertIn("TimeoutStartSec=11min", text)
        self.assertIn("logs/routine-sweep.log", text)

    def test_timer_shape(self) -> None:
        text = render_timer(self.routine(jitter_seconds=0))
        self.assertIn("OnCalendar=Mon..Fri 05:30:00", text)
        self.assertIn("Persistent=true", text)
        self.assertNotIn("RandomizedDelaySec", text)
        self.assertIn("Unit=jarvis-routine-sweep.service", text)
        self.assertIn("WantedBy=timers.target", text)
        with_jitter = render_timer(self.routine(jitter_seconds=90))
        self.assertIn("RandomizedDelaySec=90", with_jitter)

    def test_disabled_marker(self) -> None:
        units = render_units({"sweep": self.routine(enabled=False)})
        self.assertTrue(
            units["jarvis-routine-sweep.timer"].startswith(
                "# jarvis-routine-enabled: false\n"
            )
        )
        enabled = render_units({"sweep": self.routine()})
        self.assertNotIn(
            "jarvis-routine-enabled", enabled["jarvis-routine-sweep.timer"]
        )


class RunnerTests(_TempData):
    def _script(self, body: str) -> Path:
        path = Path(self.tmp.name) / "job.py"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def _routine(self, script: Path, **over: object) -> Routine:
        base: dict[str, object] = {
            "name": "job",
            "description": "d",
            "schedule": "daily",
            "kind": "shell",
            "script": script.name,
            "timeout_minutes": 1,
        }
        base.update(over)
        return Routine(**base)  # type: ignore[arg-type]

    def _job_events(self) -> list[dict[str, object]]:
        return [e["payload"] for e in event_log.fetch_tail(50) if e["type"] == "job"]

    def test_success_records_summary(self) -> None:
        script = self._script("""
            print("working...")
            print("done: 2 repos, 0 drift")
            """)
        code = run_routine.run(self._routine(script), Path(self.tmp.name))
        self.assertEqual(code, 0)
        events = self._job_events()
        self.assertEqual([e["phase"] for e in events], ["start", "end"])
        self.assertTrue(events[1]["ok"])
        self.assertEqual(events[1]["summary"], "done: 2 repos, 0 drift")
        state = Path(self.tmp.name) / "routines" / "job"
        last = json.loads((state / "last.json").read_text())
        self.assertTrue(last["ok"])
        self.assertEqual(len(list((state / "runs").glob("*.log"))), 1)

    def test_failure_is_a_failed_job(self) -> None:
        script = self._script("""
            import sys
            print("could not clone")
            sys.exit(3)
            """)
        with self.assertRaises(RuntimeError):
            run_routine.run(self._routine(script), Path(self.tmp.name))
        end = self._job_events()[-1]
        self.assertFalse(end["ok"])
        self.assertEqual(end["exit"], 3)
        self.assertIn("could not clone", str(end["summary"]))

    def test_timeout_kills_and_fails(self) -> None:
        script = self._script("""
            import time
            print("starting", flush=True)
            time.sleep(30)
            """)
        routine = self._routine(script, timeout_minutes=1)
        # Shrink the real timeout without changing the registry contract.
        original = subprocess.run

        def fast(*a: object, **kw: object) -> object:
            kw["timeout"] = 0.5
            return original(*a, **kw)  # type: ignore[arg-type]

        run_routine.subprocess.run = fast  # type: ignore[assignment]
        self.addCleanup(setattr, run_routine.subprocess, "run", original)
        with self.assertRaises(RuntimeError):
            run_routine.run(routine, Path(self.tmp.name))
        end = self._job_events()[-1]
        self.assertFalse(end["ok"])
        self.assertEqual(end["exit"], 124)
        self.assertIn("timed out", str(end["summary"]))

    def test_dry_run_and_unknown_name(self) -> None:
        registry = self.write_registry("""
            routines:
              only:
                description: d
                schedule: daily
                kind: shell
                script: scripts/x.py
                args: [--flag]
            """)
        self.assertEqual(
            run_routine.main(["only", "--config", str(registry), "--dry-run"]), 0
        )
        self.assertEqual(run_routine.main(["nope", "--config", str(registry)]), 2)


class SweepDiffTests(_TempData):
    def _result(self, checks: dict[str, bool]) -> conformance_sweep.RepoResult:
        r = conformance_sweep.RepoResult(name="atlas", url="u", branch=None)
        r.checks = dict(checks)
        r.commit = "abc1234"
        r.total = len(checks)
        r.passing = sum(checks.values())
        return r

    def test_repo_spec_parsing(self) -> None:
        spec = conformance_sweep.RepoSpec.parse(
            "atlas=https://github.com/barelysmash/atlas@develop"
        )
        self.assertEqual((spec.name, spec.branch), ("atlas", "develop"))
        self.assertIsNone(conformance_sweep.RepoSpec.parse("a=https://x/y").branch)
        with self.assertRaises(argparse.ArgumentTypeError):
            conformance_sweep.RepoSpec.parse("no-equals-sign")

    def test_first_run_has_no_changes(self) -> None:
        r = self._result({"a": True, "b": False})
        conformance_sweep.diff_against(r, None)
        self.assertFalse(r.changed)
        self.assertEqual(r.score, "1/2")

    def test_classification(self) -> None:
        previous = {
            "commit": "old0000",
            "checks": {"a": True, "b": False, "gone": True},
        }
        r = self._result({"a": False, "b": True, "new": True})
        conformance_sweep.diff_against(r, previous)
        self.assertEqual(r.regressed, ["a"])
        self.assertEqual(r.fixed, ["b"])
        self.assertEqual(r.added, ["new"])
        self.assertEqual(r.removed, ["gone"])
        self.assertEqual(r.previous_commit, "old0000")
        self.assertIn("regressed: a", conformance_sweep.change_summary(r))

    def test_events_only_on_change(self) -> None:
        quiet = self._result({"a": True})
        conformance_sweep.emit_changes(quiet)
        self.assertEqual(
            [e for e in event_log.fetch_tail(10) if e["type"] == "agent"], []
        )
        loud = self._result({"a": False})
        loud.regressed = ["a"]
        conformance_sweep.emit_changes(loud)
        agent = [e for e in event_log.fetch_tail(10) if e["type"] == "agent"][-1]
        self.assertEqual(agent["payload"]["kind"], "regression")
        self.assertEqual(agent["payload"]["severity"], "warning")
        self.assertEqual(agent["payload"]["data"]["failing"], ["a"])

    def test_history_round_trip(self) -> None:
        history = Path(self.tmp.name) / "history"
        first = self._result({"a": True, "b": True})
        conformance_sweep.record(history, first, "20260101T000000Z")
        previous = conformance_sweep.load_previous(history, "atlas")
        assert previous is not None
        second = self._result({"a": True, "b": False})
        conformance_sweep.diff_against(second, previous)
        self.assertEqual(second.regressed, ["b"])
        self.assertEqual(second.previous_commit, "abc1234")

    def test_one_liner(self) -> None:
        ok = self._result({"a": True})
        bad = self._result({"a": False})
        bad.name = "jarvis-v2"
        bad.regressed = ["a"]
        err = conformance_sweep.RepoResult(name="x", url="u", branch=None, error="boom")
        line = conformance_sweep.one_liner([ok, bad, err], "deadbeefcafe")
        self.assertEqual(
            line, "atlas 1/1 (=) · jarvis-v2 0/1 (▲) · x ERR · jam@deadbee"
        )


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.name == "posix", "bash script; runs where it is deployed")
class InstallUnitsScriptTests(_TempData):
    """scripts/install_routine_units.sh against a fake systemctl.

    The fake logs every call and the test reads the log back, so the
    install / enable / disable / orphan sequence is pinned without systemd.
    """

    SCRIPT = REPO / "scripts" / "install_routine_units.sh"

    def _run(self, checkout: Path, unit_dir: Path) -> tuple[str, list[str]]:
        log = Path(self.tmp.name) / "systemctl.log"
        fake = Path(self.tmp.name) / "fake-systemctl"
        fake.write_text(
            f'#!/usr/bin/env bash\necho "$*" >> "{log}"\n', encoding="utf-8"
        )
        fake.chmod(0o755)
        proc = subprocess.run(
            ["bash", str(self.SCRIPT), str(checkout)],
            env={
                **os.environ,
                "JARVIS_SYSTEMD_USER_DIR": str(unit_dir),
                "JARVIS_SYSTEMCTL": str(fake),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = log.read_text().splitlines() if log.exists() else []
        return proc.stdout, calls

    def _checkout(self, units: dict[str, str]) -> Path:
        checkout = Path(self.tmp.name) / "checkout"
        (checkout / "deploy" / "systemd").mkdir(parents=True)
        for name, body in units.items():
            (checkout / "deploy" / "systemd" / name).write_text(body)
        return checkout

    def test_installs_and_enables(self) -> None:
        checkout = self._checkout(
            {
                "jarvis-routine-a.service": "[Service]\n",
                "jarvis-routine-a.timer": "[Timer]\n",
            }
        )
        unit_dir = Path(self.tmp.name) / "units"
        out, calls = self._run(checkout, unit_dir)
        self.assertTrue((unit_dir / "jarvis-routine-a.service").is_file())
        self.assertTrue((unit_dir / "jarvis-routine-a.timer").is_file())
        self.assertEqual(
            calls,
            ["--user daemon-reload", "--user enable --now jarvis-routine-a.timer"],
        )
        self.assertIn("2 unit file(s) installed, 0 orphan(s) removed", out)

    def test_disabled_marker_and_orphan(self) -> None:
        checkout = self._checkout(
            {
                "jarvis-routine-b.service": "[Service]\n",
                "jarvis-routine-b.timer": "# jarvis-routine-enabled: false\n[Timer]\n",
            }
        )
        unit_dir = Path(self.tmp.name) / "units"
        unit_dir.mkdir()
        (unit_dir / "jarvis-routine-old.timer").write_text("[Timer]\n")
        (unit_dir / "jarvis-routine-old.service").write_text("[Service]\n")
        out, calls = self._run(checkout, unit_dir)
        self.assertFalse((unit_dir / "jarvis-routine-old.timer").exists())
        self.assertFalse((unit_dir / "jarvis-routine-old.service").exists())
        self.assertEqual(
            calls,
            [
                "--user disable --now jarvis-routine-old.timer",
                "--user daemon-reload",
                "--user disable --now jarvis-routine-b.timer",
            ],
        )
        self.assertIn("1 orphan(s) removed", out)

    def test_committed_units_install_cleanly(self) -> None:
        unit_dir = Path(self.tmp.name) / "units"
        _, calls = self._run(REPO, unit_dir)
        self.assertIn(
            "--user enable --now jarvis-routine-conformance-sweep.timer", calls
        )
