"""Backlog drain (ADR-0003): the loader, the pick rule, and one full fire."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "routines"))
sys.path.insert(0, str(REPO / "tests"))

import backlog_drain  # noqa: E402
from test_goal_runner import (  # noqa: E402
    FIXED,
    PY,
    FakeClient,
    git,
    make_target,
    reply,
    text,
    tool,
)

from orchestrator import event_log  # noqa: E402
from orchestrator.routines import (  # noqa: E402
    RoutineConfigError,
    load_backlog,
    load_routines,
)

TASK = "Make add() add"


def entry(name: str, url: str, **over: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "description": f"{name} desc",
        "task": TASK,
        "repo": url,
        "condition": f"{PY} check.py",
        "commands": {"check": f"{PY} check.py"},
        "paths": ["src/**"],
        "budget": {"iterations": 4, "minutes": 5, "tokens": 100000},
        "max_changed_lines": 50,
    }
    item.update(over)
    return {k: v for k, v in item.items() if v is not None}


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._old = os.environ.get("JARVIS_DATA_DIR")
        os.environ["JARVIS_DATA_DIR"] = str(self.root / "data")
        self.addCleanup(self._restore)
        event_log._schema_ready_for = None
        self.state = self.root / "routines" / "backlog-drain"
        self.state.mkdir(parents=True)

    def _restore(self) -> None:
        if self._old is None:
            os.environ.pop("JARVIS_DATA_DIR", None)
        else:
            os.environ["JARVIS_DATA_DIR"] = self._old

    def backlog(self, *items: dict[str, Any]) -> Path:
        path = self.root / "backlog.yaml"
        path.write_text(yaml.safe_dump({"backlog": list(items)}))
        return path


class LoaderTests(_Fixture):
    def test_loads_in_order_with_goal_fields(self) -> None:
        entries = load_backlog(
            self.backlog(entry("a", "file:///x"), entry("b", "file:///x", after=["a"]))
        )
        self.assertEqual([e.name for e in entries], ["a", "b"])
        self.assertEqual(entries[0].routine_name, "backlog-a")
        self.assertEqual(entries[0].task, TASK)
        self.assertEqual(entries[0].goal.commands["condition"], f"{PY} check.py")
        self.assertEqual(entries[1].after, ("a",))

    def test_rejects(self) -> None:
        cases = {
            "duplicate": [entry("a", "file:///x"), entry("a", "file:///x")],
            "names itself": [entry("a", "file:///x", after=["a"])],
            "must be an earlier entry": [entry("a", "file:///x", after=["zz"])],
            "unknown key": [entry("a", "file:///x", schedule="Sun")],
            "'paths'": [entry("a", "file:///x", paths=[])],
            "name must match": [entry("Bad_Name", "file:///x")],
        }
        for needle, items in cases.items():
            with self.subTest(needle), self.assertRaises(RoutineConfigError) as ctx:
                load_backlog(self.backlog(*items))
            self.assertIn(needle, str(ctx.exception))

    def test_empty_backlog(self) -> None:
        path = self.root / "backlog.yaml"
        path.write_text("backlog:\n")
        self.assertEqual(load_backlog(path), [])

    def test_repo_backlog_and_drain_routine_agree(self) -> None:
        entries = load_backlog(REPO / "config" / "backlog.yaml")
        self.assertTrue(entries)
        drain = load_routines(REPO / "config" / "routines.yaml")["backlog-drain"]
        self.assertEqual(drain.kind, "shell")
        self.assertEqual(drain.script, "scripts/routines/backlog_drain.py")
        longest = max(e.goal.budget["minutes"] for e in entries)
        self.assertGreater(drain.timeout_minutes, longest + 5)
        for e in entries:
            self.assertTrue(e.task, f"{e.name}: give it a task line")
            tasks = (REPO / "TASKS.md").read_text(encoding="utf-8").splitlines()
            hits = [ln for ln in tasks if ln.strip() == f"- [ ] {e.task}"]
            self.assertEqual(
                len(hits), 1, f"{e.name}: task line must be in TASKS.md once"
            )


class PickTests(_Fixture):
    def entries(self, *items: dict[str, Any]) -> list[Any]:
        return load_backlog(self.backlog(*items))

    def test_pending_first_in_file_order(self) -> None:
        es = self.entries(entry("a", "file:///x"), entry("b", "file:///x"))
        chosen, notes = backlog_drain.pick(es, backlog_drain.Queue())
        assert chosen is not None
        self.assertEqual(chosen.name, "a")
        self.assertEqual(notes, [("a", "pending"), ("b", "pending")])

    def test_states(self) -> None:
        es = self.entries(entry("a", "file:///x"))
        q = backlog_drain.Queue()
        rec = q.record("a")
        spec = backlog_drain.spec_hash(es[0])

        rec.state, rec.base = "green", "aaa"
        chosen, notes = backlog_drain.pick(es, q, lookup_base=lambda r, b: "aaa")
        self.assertIsNone(chosen)
        self.assertIn("patch waiting", notes[0][1])
        chosen, notes = backlog_drain.pick(es, q, lookup_base=lambda r, b: "bbb")
        self.assertIsNotNone(chosen)
        self.assertIn("base moved", notes[0][1])
        chosen, notes = backlog_drain.pick(es, q, lookup_base=lambda r, b: "")
        self.assertIsNone(chosen)
        self.assertIn("unreachable", notes[0][1])

        rec.state = "landed"
        self.assertIsNone(backlog_drain.pick(es, q)[0])

        rec.state, rec.spec, rec.outcome = "parked", spec, "stalled"
        chosen, notes = backlog_drain.pick(es, q)
        self.assertIsNone(chosen)
        self.assertEqual(notes[0][1], "parked (stalled)")
        rec.spec = "edited"
        chosen, notes = backlog_drain.pick(es, q)
        self.assertIsNotNone(chosen)
        self.assertEqual(notes[0][1], "parked; entry edited")

    def test_after_holds_until_landed(self) -> None:
        es = self.entries(entry("a", "file:///x"), entry("b", "file:///x", after=["a"]))
        q = backlog_drain.Queue()
        q.record("a").state = "parked"
        q.record("a").spec = backlog_drain.spec_hash(es[0])
        chosen, notes = backlog_drain.pick(es, q)
        self.assertIsNone(chosen)
        self.assertEqual(notes[1], ("b", "after a (parked)"))
        q.record("a").state = "landed"
        chosen, _ = backlog_drain.pick(es, q)
        assert chosen is not None
        self.assertEqual(chosen.name, "b")

    def test_spec_hash_tracks_the_entry_as_written(self) -> None:
        a = self.entries(entry("a", "file:///x"))[0]
        b = self.entries(entry("a", "file:///x", max_changed_lines=51))[0]
        c = self.entries(entry("a", "file:///x"))[0]
        self.assertEqual(backlog_drain.spec_hash(a), backlog_drain.spec_hash(c))
        self.assertNotEqual(backlog_drain.spec_hash(a), backlog_drain.spec_hash(b))


class DrainTests(_Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.url = make_target(self.root)
        self.target = self.root / "target"
        (self.target / "TASKS.md").write_text(
            f"# Tasks\n\n- [ ] Something else\n- [ ] {TASK}\n", encoding="utf-8"
        )
        git("add", "-A", cwd=self.target)
        git("commit", "-q", "-m", "tasks", cwd=self.target)

    def green_client(self) -> FakeClient:
        return FakeClient(
            reply(
                tool("write_file", path="src/pkg/calc.py", content=FIXED),
                stop="tool_use",
            ),
            reply(tool("run", name="check"), stop="tool_use"),
            reply(text("Fixed.")),
        )

    def test_green_run_ticks_task_and_parks_until_base_moves(self) -> None:
        es = load_backlog(self.backlog(entry("fix-add", self.url)))
        summary, code = backlog_drain.drain_once(
            es, self.state, client=self.green_client()
        )
        self.assertEqual(code, 0, summary)
        self.assertIn("backlog: fix-add -> green", summary)
        self.assertIn("task ticked in TASKS.md", summary)

        q = backlog_drain.Queue.load(self.state / "queue.json")
        rec = q.entries["fix-add"]
        self.assertEqual(rec.state, "green")
        self.assertTrue(rec.ticked)
        self.assertEqual(rec.branch, "goal/backlog-fix-add")
        # the entry's state is a sibling routine dir, so the lock dir is shared
        checkout = self.root / "routines" / "backlog-fix-add" / "repo"
        self.assertTrue((checkout / ".git").is_dir())
        self.assertEqual(git("rev-parse", "HEAD", cwd=checkout).strip(), rec.base)
        self.assertEqual(git("status", "--porcelain", cwd=checkout), "")
        # one commit on the branch; it carries the fix and the tick
        log = git(
            "log", "--format=%s", f"{rec.base}..goal/backlog-fix-add", cwd=checkout
        )
        self.assertEqual(log.split(), ["goal:", "backlog-fix-add:", "green"])
        files = git("show", "--stat", "--format=", "goal/backlog-fix-add", cwd=checkout)
        self.assertIn("TASKS.md", files)
        self.assertIn("calc.py", files)
        # the patch applies on a fresh clone and ticks exactly the right line
        fresh = self.root / "fresh"
        git("clone", "-q", self.url, str(fresh), cwd=self.root)
        git("am", "-q", rec.patch, cwd=fresh)
        tasks = (fresh / "TASKS.md").read_text(encoding="utf-8")
        self.assertIn("- [ ] Something else", tasks)
        self.assertIn(f"- [x] {TASK} — DONE {rec.last_run[:10]} (backlog-drain)", tasks)
        self.assertEqual((fresh / "src" / "pkg" / "calc.py").read_text(), FIXED)

        # same base: nothing to do, and no model call
        summary, code = backlog_drain.drain_once(
            es, self.state, client=FakeClient(reply(text("should not be called")))
        )
        self.assertEqual(code, 0)
        self.assertIn("nothing eligible", summary)
        self.assertIn("1 awaiting landing", summary)

        # the fix lands upstream: base moves, re-run finds already_green -> landed
        (self.target / "src" / "pkg" / "calc.py").write_text(FIXED)
        git("commit", "-qam", "land the fix", cwd=self.target)
        summary, code = backlog_drain.drain_once(
            es, self.state, client=FakeClient(reply(text("should not be called")))
        )
        self.assertEqual(code, 0, summary)
        self.assertIn("fix-add -> already_green", summary)
        q = backlog_drain.Queue.load(self.state / "queue.json")
        self.assertEqual(q.entries["fix-add"].state, "landed")
        summary, _ = backlog_drain.drain_once(es, self.state)
        self.assertIn("1 landed", summary)

    def test_task_line_missing_is_reported_not_fatal(self) -> None:
        es = load_backlog(self.backlog(entry("fix-add", self.url, task="No such line")))
        summary, code = backlog_drain.drain_once(
            es, self.state, client=self.green_client()
        )
        self.assertEqual(code, 0)
        self.assertIn("-> green", summary)
        self.assertIn("task not ticked: 0 match(es)", summary)
        self.assertFalse(
            backlog_drain.Queue.load(self.state / "queue.json")
            .entries["fix-add"]
            .ticked
        )

    def test_non_green_parks_until_edited(self) -> None:
        es = load_backlog(self.backlog(entry("fix-add", self.url)))
        stuck = FakeClient(reply(tool("run", name="check"), stop="tool_use"))
        summary, code = backlog_drain.drain_once(es, self.state, client=stuck)
        self.assertEqual(code, 0, summary)
        self.assertIn("fix-add -> stalled", summary)
        q = backlog_drain.Queue.load(self.state / "queue.json")
        self.assertEqual(q.entries["fix-add"].state, "parked")
        summary, _ = backlog_drain.drain_once(es, self.state, client=stuck)
        self.assertIn("nothing eligible", summary)
        self.assertIn("1 parked", summary)
        edited = load_backlog(
            self.backlog(entry("fix-add", self.url, max_changed_lines=60))
        )
        summary, _ = backlog_drain.drain_once(
            edited, self.state, client=self.green_client()
        )
        self.assertIn("fix-add -> green", summary)

    def test_only_and_dry_run_and_removed_entries(self) -> None:
        es = load_backlog(self.backlog(entry("fix-add", self.url)))
        summary, code = backlog_drain.drain_once(es, self.state, dry_run=True)
        self.assertEqual((summary, code), ("backlog: would run fix-add", 0))
        self.assertFalse((self.state / "queue.json").is_file())
        summary, code = backlog_drain.drain_once(es, self.state, only="nope")
        self.assertEqual(code, 2)
        backlog_drain.drain_once(es, self.state, client=self.green_client())
        # removing the entry from the backlog drops its record on the next fire
        summary, _ = backlog_drain.drain_once([], self.state)
        self.assertEqual(
            backlog_drain.Queue.load(self.state / "queue.json").entries, {}
        )
        self.assertIn("nothing eligible", summary)

    def test_cli_probe(self) -> None:
        path = self.backlog(entry("fix-add", self.url))
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = backlog_drain.main(
                [
                    "--backlog",
                    str(path),
                    "--state",
                    str(self.state),
                    "--probe",
                    "fix-add",
                ]
            )
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("backlog-fix-add: probe — condition is red", out.getvalue())
        self.assertIn("FAIL: add(2, 3)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
