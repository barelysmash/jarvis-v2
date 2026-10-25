"""Goal Runner (ADR-0002), offline.

A tiny repository with a bug, a condition script that checks it, and a
scripted stand-in for the model. Every outcome and every rail in the ADR
is pinned here: already_green, green, stalled, budget, too_large, error
(lock held), probe; protected paths, edit-scope allowlist, named commands
only; squash-to-one-commit hand-off with an applicable patch; HEAD
detached and tree clean afterwards; setup runs once per checkout.
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
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import run_routine  # noqa: E402

from orchestrator import event_log, goal_runner  # noqa: E402
from orchestrator.routines import (  # noqa: E402
    RoutineConfigError,
    load_routines,
    render_service,
)

PY = Path(sys.executable).as_posix()

BUGGY = "def add(a: int, b: int) -> int:\n    return a - b\n"
FIXED = "def add(a: int, b: int) -> int:\n    return a + b\n"
CHECK = textwrap.dedent(
    """\
    import sys
    sys.path.insert(0, "src")
    from pkg.calc import add
    got = add(2, 3)
    if got != 5:
        print(f"FAIL: add(2, 3) returned {got}, expected 5")
        sys.exit(1)
    print("ok")
    """
)


def git(*args: str, cwd: Path) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.check_output(["git", *args], cwd=cwd, env=env, text=True)


def make_target(root: Path, *, fixed: bool = False) -> str:
    """The repository a goal routine works on. Returns its file:// URL."""

    path = root / "target"
    (path / "src" / "pkg").mkdir(parents=True)
    (path / "tests").mkdir()
    git("init", "-q", "-b", "main", cwd=path)
    (path / "src" / "pkg" / "__init__.py").write_text("")
    (path / "src" / "pkg" / "calc.py").write_text(FIXED if fixed else BUGGY)
    (path / "check.py").write_text(CHECK)
    (path / "tests" / "test_calc.py").write_text("def test_nothing():\n    pass\n")
    (path / "pyproject.toml").write_text('[project]\nname = "pkg"\n')
    (path / "README.md").write_text("# target\n")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "init", cwd=path)
    return path.as_uri()


def reply(
    *blocks: dict[str, Any], stop: str = "end_turn", tokens: int = 100
) -> goal_runner.ModelReply:
    return goal_runner.ModelReply(
        content=list(blocks),
        stop_reason=stop,
        input_tokens=tokens,
        output_tokens=tokens // 10,
        model="fake-model",
    )


def text(s: str) -> dict[str, Any]:
    return {"type": "text", "text": s}


_calls = 0


def tool(tool_name: str, /, **inputs: Any) -> dict[str, Any]:
    global _calls
    _calls += 1
    return {
        "type": "tool_use",
        "id": f"call_{_calls}",
        "name": tool_name,
        "input": inputs,
    }


class FakeClient:
    """Returns scripted replies in order; the last one repeats forever."""

    def __init__(self, *replies: goal_runner.ModelReply) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, Any]]] = []

    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> goal_runner.ModelReply:
        self.calls.append([dict(m) for m in messages])
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0]


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._old = os.environ.get("JARVIS_DATA_DIR")
        os.environ["JARVIS_DATA_DIR"] = str(self.root / "data")
        self.addCleanup(self._restore)
        event_log._schema_ready_for = None
        self.state = self.root / "routines" / "fix-add"
        self.state.mkdir(parents=True)

    def _restore(self) -> None:
        if self._old is None:
            os.environ.pop("JARVIS_DATA_DIR", None)
        else:
            os.environ["JARVIS_DATA_DIR"] = self._old

    def registry(self, url: str, **over: Any) -> Path:
        spec: dict[str, Any] = {
            "description": "Make add() add.",
            "schedule": "Sat 06:00:00",
            "kind": "goal",
            "repo": url,
            "condition": f"{PY} check.py",
            "commands": {"check": f"{PY} check.py"},
            "paths": ["src/**"],
            "budget": {"iterations": 4, "minutes": 5, "tokens": 100000},
            "max_changed_lines": 50,
            "timeout_minutes": 6,
        }
        spec.update(over)
        spec = {k: v for k, v in spec.items() if v is not None}
        import yaml

        path = self.root / "routines.yaml"
        path.write_text(yaml.safe_dump({"routines": {"fix-add": spec}}))
        return path

    def routine(self, url: str, **over: Any) -> Any:
        return load_routines(self.registry(url, **over))["fix-add"]

    def run_goal(
        self, url: str, client: FakeClient | None, **over: Any
    ) -> goal_runner.GoalResult:
        return goal_runner.run_goal(
            self.routine(url, **over), state=self.state, client=client
        )

    def agent_events(self) -> list[dict[str, Any]]:
        return [e["payload"] for e in event_log.fetch_tail(200) if e["type"] == "agent"]


class RegistryTests(_Fixture):
    def test_goal_spec_parses_with_defaults(self) -> None:
        r = self.routine(
            "file:///x", budget=None, max_changed_lines=None, timeout_minutes=25
        )
        assert r.goal is not None
        self.assertEqual(r.kind, "goal")
        self.assertEqual(r.goal.base, "main")
        self.assertEqual(
            r.goal.budget, {"iterations": 10, "minutes": 20, "tokens": 400000}
        )
        self.assertEqual(r.goal.max_changed_lines, 400)
        self.assertEqual(r.goal.commands["condition"], f"{PY} check.py")
        self.assertEqual(r.args, ("fix-add",))
        self.assertIn("run_routine.py fix-add", render_service(r))

    def test_rejections(self) -> None:
        cases = {
            "no paths": {"paths": []},
            "timeout not above minutes": {"timeout_minutes": 5},
            "bad budget key": {"budget": {"hours": 1}},
            "condition mismatch": {"commands": {"condition": "echo no"}},
            "bad command name": {"commands": {"Bad Name": "echo"}},
            "escaping path": {"paths": ["../x/**"]},
        }
        for label, over in cases.items():
            with self.subTest(label), self.assertRaises(RoutineConfigError):
                self.routine("file:///x", **over)

    def test_shell_kind_still_requires_script(self) -> None:
        with self.assertRaises(RoutineConfigError):
            self.routine("file:///x", kind="shell", script=None)


class SandboxTests(_Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.url = make_target(self.root)
        r = self.routine(self.url, unprotect=["tests/fixtures/**"])
        self.checkout, self.base = goal_runner.ensure_checkout(self.state, r.goal)
        self.sb = goal_runner.Sandbox(self.checkout, r.goal, self.base)

    def test_protected_set(self) -> None:
        protected = [
            "check.py",  # named by the condition
            "tests/test_calc.py",
            "pyproject.toml",
            ".github/workflows/ci.yml",
            "src/pkg/conftest.py",
            "src/pkg/test_calc.py",
            "schemas/decision.schema.json",
            "jam.yaml",
            "deploy/x.sh",
        ]
        for rel in protected:
            with self.subTest(rel):
                self.assertTrue(self.sb.is_protected(rel))
        self.assertFalse(self.sb.is_protected("src/pkg/calc.py"))
        self.assertFalse(
            self.sb.is_protected("tests/fixtures/data.json")
        )  # unprotected

    def test_scope_and_paths(self) -> None:
        self.assertTrue(self.sb.is_allowed("src/pkg/calc.py"))
        self.assertFalse(self.sb.is_allowed("README.md"))
        for bad in ("/etc/passwd", "../x", "src/../../x", ".git/config", "C:\\x"):
            with self.subTest(bad), self.assertRaises(goal_runner.GoalError):
                self.sb.relative(bad)

    def test_tools(self) -> None:
        self.assertIn("return a - b", self.sb.tool_read_file("src/pkg/calc.py"))
        self.assertIn("src/pkg/calc.py", self.sb.tool_list_files("src/**/*.py"))
        self.assertTrue(self.sb.tool_write_file("check.py", "x").startswith("REFUSED"))
        self.assertTrue(self.sb.tool_write_file("README.md", "x").startswith("REFUSED"))
        self.assertTrue(self.sb.tool_run("rm").startswith("ERROR: no command named"))
        out = self.sb.tool_run("check")
        self.assertIn("exit 1", out)
        self.assertIn("FAIL: add(2, 3) returned -1", out)
        self.assertTrue(
            self.sb.tool_write_file("src/pkg/calc.py", FIXED).startswith("wrote")
        )
        self.assertIn("exit 0", self.sb.tool_run("check"))


class RunTests(_Fixture):
    def test_already_green(self) -> None:
        url = make_target(self.root, fixed=True)
        res = self.run_goal(url, FakeClient())
        self.assertEqual(res.outcome, "already_green")
        self.assertEqual(res.iterations, 0)
        self.assertEqual(res.patch, "")

    def test_probe_runs_condition_only(self) -> None:
        url = make_target(self.root)
        res = goal_runner.run_goal(
            self.routine(url), state=self.state, client=FakeClient(), probe=True
        )
        self.assertEqual(res.outcome, "probe")
        self.assertIn("FAIL: add(2, 3)", res.last_output)
        self.assertEqual(git("branch", "--list", "goal/*", cwd=self.state / "repo"), "")
        self.assertEqual(self.agent_events(), [])

    def test_green_in_one_iteration_hands_off_branch_and_patch(self) -> None:
        url = make_target(self.root)
        client = FakeClient(
            reply(
                text("Reading."),
                tool("read_file", path="src/pkg/calc.py"),
                stop="tool_use",
            ),
            reply(
                tool("write_file", path="src/pkg/calc.py", content=FIXED),
                stop="tool_use",
            ),
            reply(tool("run", name="check"), stop="tool_use"),
            reply(text("Fixed the operator.")),
        )
        res = self.run_goal(url, client)
        self.assertEqual(res.outcome, "green", res.error)
        self.assertEqual(res.iterations, 1)
        self.assertEqual(res.files, ["src/pkg/calc.py"])
        self.assertEqual(res.changed_lines, 2)
        self.assertEqual(res.branch, "goal/fix-add")
        checkout = self.state / "repo"
        # one squashed commit on the branch, with an explanatory message
        log = git(
            "log", "--format=%s", f"{res.base}..goal/fix-add", cwd=checkout
        ).split("\n")
        self.assertEqual([line for line in log if line], ["goal: fix-add: green"])
        body = git("log", "-1", "--format=%B", "goal/fix-add", cwd=checkout)
        self.assertIn("outcome: green after 1 iteration(s)", body)
        self.assertIn("transcript.jsonl", body)
        # patch applies on a fresh clone
        fresh = self.root / "fresh"
        git("clone", "-q", url, str(fresh), cwd=self.root)
        git("am", "-q", res.patch, cwd=fresh)
        self.assertEqual((fresh / "src" / "pkg" / "calc.py").read_text(), FIXED)
        # HEAD detached at base, tree clean
        self.assertEqual(git("rev-parse", "HEAD", cwd=checkout).strip(), res.base)
        self.assertEqual(git("status", "--porcelain", cwd=checkout), "")
        # transcript + result + event
        lines = [
            json.loads(line)
            for line in Path(res.run_dir, "transcript.jsonl").read_text().splitlines()
        ]
        kinds = [rec["kind"] for rec in lines]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds[-1], "end")
        self.assertIn("tool", kinds)
        self.assertEqual(sum(1 for rec in lines if rec["kind"] == "condition"), 2)
        self.assertTrue(Path(res.run_dir, "result.json").is_file())
        ev = self.agent_events()[-1]
        self.assertEqual(ev["kind"], "goal")
        self.assertEqual(ev["severity"], "notice")
        self.assertIn("fix-add: green", ev["summary"])
        # the model saw the condition output in its first message
        self.assertIn("FAIL: add(2, 3) returned -1", client.calls[0][0]["content"])

    def test_protected_and_out_of_scope_writes_are_refused(self) -> None:
        url = make_target(self.root)
        client = FakeClient(
            reply(
                tool("write_file", path="check.py", content="print('ok')"),
                stop="tool_use",
            ),
            reply(
                tool("write_file", path="tests/test_calc.py", content=""),
                stop="tool_use",
            ),
            reply(tool("write_file", path="README.md", content="x"), stop="tool_use"),
            reply(text("Giving up.")),
        )
        res = self.run_goal(
            url, client, budget={"iterations": 1, "minutes": 5, "tokens": 100000}
        )
        self.assertEqual(res.outcome, "budget")
        self.assertEqual(res.files, [])
        self.assertEqual(res.commit, "")
        checkout = self.state / "repo"
        self.assertEqual((checkout / "check.py").read_text(), CHECK)
        # The last call's messages hold every tool_result of the run.
        results = [m for m in client.calls[-1] if isinstance(m.get("content"), list)]
        refusals = [
            b["content"]
            for m in results
            for b in m["content"]
            if isinstance(b, dict)
            and b.get("type") == "tool_result"
            and str(b["content"]).startswith("REFUSED")
        ]
        self.assertEqual(len(refusals), 3)
        self.assertIn("protected", refusals[0])
        self.assertIn("outside this routine's edit scope", refusals[2])

    def test_stalled_when_condition_output_repeats(self) -> None:
        url = make_target(self.root)
        client = FakeClient(reply(text("I don't know what to change.")))
        res = self.run_goal(url, client)
        self.assertEqual(res.outcome, "stalled")
        self.assertEqual(res.iterations, 2)  # initial + two identical re-runs
        self.assertEqual(res.commit, "")
        self.assertEqual(self.agent_events()[-1]["severity"], "warning")

    def test_token_budget(self) -> None:
        url = make_target(self.root)
        # Each iteration edits something different so the output never repeats.
        client = FakeClient(
            reply(
                tool(
                    "write_file",
                    path="src/pkg/calc.py",
                    content=BUGGY.replace("a - b", "a * b"),
                ),
                stop="tool_use",
                tokens=600,
            ),
            reply(text("try again"), tokens=600),
        )
        res = self.run_goal(
            url, client, budget={"iterations": 4, "minutes": 5, "tokens": 1000}
        )
        self.assertEqual(res.outcome, "budget")
        self.assertIn("tokens", res.error)
        self.assertEqual(res.iterations, 1)

    def test_too_large_refuses_and_stops(self) -> None:
        url = make_target(self.root)
        big = "\n".join(f"x{i} = {i}" for i in range(80)) + "\n"
        client = FakeClient(
            reply(
                tool("write_file", path="src/pkg/extra.py", content=big),
                stop="tool_use",
            ),
            reply(text("done")),
        )
        res = self.run_goal(url, client, max_changed_lines=20)
        self.assertEqual(res.outcome, "too_large")
        self.assertFalse((self.state / "repo" / "src" / "pkg" / "extra.py").exists())
        self.assertEqual(res.commit, "")

    def test_lock_held_is_an_error(self) -> None:
        url = make_target(self.root)
        lock = goal_runner._lock_path(self.state, url)
        lock.write_text("someone else\n")
        res = self.run_goal(url, FakeClient())
        self.assertEqual(res.outcome, "error")
        self.assertIn("holds the lock", res.error)
        self.assertTrue(lock.exists(), "a foreign lock must not be removed")

    def test_commands_run_in_the_routine_venv(self) -> None:
        url = make_target(self.root, fixed=True)
        # `python` unqualified must be the routine's own interpreter, never the
        # one running JARVIS (and never the production venv).
        routine = self.routine(
            url,
            condition='python -c "import sys; print(sys.prefix); sys.exit(1)"',
            commands={},
        )
        res = goal_runner.run_goal(
            routine, state=self.state, client=FakeClient(), probe=True
        )
        self.assertEqual(res.outcome, "probe")
        self.assertIn(
            (self.state / "venv").resolve().as_posix().lower(),
            res.last_output.replace("\\", "/").lower(),
        )
        self.assertTrue((self.state / "venv").is_dir())

    def test_setup_runs_once_per_checkout(self) -> None:
        url = make_target(self.root, fixed=True)
        counter = (self.root / "setup-count").as_posix()
        setup = [f"{PY} -c \"open('{counter}', 'a').write('x')\""]
        self.run_goal(url, FakeClient(), setup=setup)
        self.run_goal(url, FakeClient(), setup=setup)
        self.assertEqual(Path(counter).read_text(), "x")
        self.assertTrue((self.state / "repo" / ".goal-setup").is_file())
        self.assertEqual(git("status", "--porcelain", cwd=self.state / "repo"), "")


class RunRoutineDispatchTests(_Fixture):
    def test_dry_run_shows_the_runner_command(self) -> None:
        registry = self.registry("file:///x")
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_routine.main(["fix-add", "--config", str(registry), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn(
            "orchestrator/goal_runner.py fix-add", out.getvalue().replace("\\", "/")
        )


if __name__ == "__main__":
    unittest.main()


class GoalCheckTests(unittest.TestCase):
    """scripts/goal_check.py: several commands as one condition."""

    SCRIPT = str(REPO / "scripts" / "goal_check.py")

    def run_check(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, self.SCRIPT, *argv], capture_output=True, text=True
        )

    def test_all_pass(self) -> None:
        proc = self.run_check(
            "--", "python", "-c", "print('a')", "--", "python", "-c", "print('b')"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("[1/2]", proc.stdout)
        self.assertIn("[2/2]", proc.stdout)
        self.assertIn("all 2 command(s) passed", proc.stdout)
        # bare `python` was resolved to the running interpreter
        self.assertIn(sys.executable, proc.stdout)

    def test_first_failure_stops_and_propagates_exit(self) -> None:
        proc = self.run_check(
            "--",
            "python",
            "-c",
            "import sys; print('red'); sys.exit(3)",
            "--",
            "python",
            "-c",
            "print('never')",
        )
        self.assertEqual(proc.returncode, 3)
        self.assertIn("FAILED [1/2] exit 3", proc.stdout)
        self.assertNotIn("never", proc.stdout)

    def test_no_commands(self) -> None:
        self.assertEqual(self.run_check().returncode, 2)

    def test_split(self) -> None:
        sys.path.insert(0, str(REPO / "scripts"))
        import goal_check

        self.assertEqual(
            goal_check.split_commands(["--", "a", "b", "--", "c"]), [["a", "b"], ["c"]]
        )
        self.assertEqual(goal_check.split_commands(["a"]), [["a"]])
