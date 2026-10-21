# ADR-0002: Goal Runner (run-to-green)

Status: Proposed
Date: 2026-10-21

## Context

Loops 1 and 2 (`docs/LOOPS.md`) are scripts: the conformance sweep reads,
the propagation step applies a recipe. Loop 3, run-to-green, is the first
loop that needs a model in it — iterate on a repository until a stated
condition holds (`jam validate` 11/11, `mypy --strict` clean, a named test
passing), then hand off a branch.

`TASKS.md` sketched this as "GoalRunner with evaluator model (Haiku)",
"session isolation per routine run", and `kind: goal` in `routines.yaml`.
`orchestrator/routines.py` already reserves `kind: goal` and rejects it, so
a registry entry cannot schedule nothing by mistake. This ADR fixes what the
runner is before it is built.

The things that would go wrong if it were built casually:

- it runs unattended, so every question it cannot answer is a stall, and
  every write it can make is a write nobody reviewed;
- the easiest way to make a red condition green is to delete the test,
  loosen the config, or edit the condition itself;
- the orchestrator's brain is a shared singleton (`server/api.py` builds one
  `JarvisBrain` whose `conversation` and `MemoryStore` are Barry's), and a
  background job that borrows it pollutes a conversation and a memory that
  belong to a person;
- a model asked "is this done?" can be talked into yes.

## Decision

### 1. The evaluator is the condition command, not a model

A goal is a shell command run from the repository root. Exit 0 is green.
Nothing else decides. The command is declared once in the registry and the
runner never changes it, never edits the files it lives in, and never
reads a model's opinion about whether it passed.

The only model in the loop is the **proposer**: given the condition's
output, the files it can see, and the diff so far, it proposes the next
edit. It is a capable model (the brain's default class, not Haiku): reading
a mypy error and writing the right annotation is the hard part. Haiku is
not used as a judge; "evaluator model" from the earlier sketch is dropped.
Stall detection is also mechanical: if the condition's output is unchanged
across three consecutive iterations, the run stops as `stalled`.

### 2. A run is isolated by construction

The runner is `orchestrator/goal_runner.py`: a minimal ReAct loop over the
Anthropic client with exactly the tools below. It does not instantiate
`JarvisBrain`, does not touch the shared brain's `conversation`, and does
not read or write `MemoryStore` — a run's record is its transcript and the
event log, not Barry's episodic memory. It emits `turn.start` / `llm` /
`tool` / `turn.end` events with origin `goal:<routine>` so the cockpit
shows it beside every other process.

Tools, all scoped to the run's checkout:

| tool | what it can do |
|---|---|
| `read_file(path)` | read any file in the checkout |
| `list_files(glob)` | enumerate paths in the checkout |
| `write_file(path, content)` | write a file **inside the edit scope** (below) |
| `run(name)` | run one of the registry's `commands` by name, with the routine's timeout; output clipped |

There is no arbitrary shell. Commands the proposer may run are declared by
name in the registry (`commands:`), and the condition is always among them.

### 3. Where it edits

Each goal routine owns a checkout under its state dir:
`~/jarvis-data/routines/<name>/repo/`, a shallow clone of the target at its
base branch, reset to the tip at the start of every run (HEAD detached, as
the sweep does). The run works on branch `goal/<name>` from that tip.
Nothing outside that directory is writable; the deployed JARVIS checkout
and the sweep's checkouts are not involved.

### 4. What it may not touch

The point of the loop is a green condition that *means* something. So by
default the proposer cannot write:

- the files the condition command names, and anything under `tests/`;
- CI and tool configuration: `.github/`, `pyproject.toml`, `setup.cfg`,
  `tox.ini`, `*.toml`, `*.ini`, `.pre-commit-config.yaml`;
- `jam.yaml`, `VERSION` notes and vendored `*.schema.json` (loop 2's job);
- anything under `.git/`, `deploy/`, `scripts/`.

The registry's `paths:` is an explicit allowlist of globs the proposer may
write within (e.g. `packages/atlas-core/src/**`). A routine that needs to
edit a protected file says so with `unprotect:`; that is a deliberate,
reviewed line in a committed file, not a runtime choice.

A write that would take the branch past `max_changed_lines` (default 400)
is refused and ends the run as `too_large`: a change that size is not an
unattended change.

### 5. Budget

Three hard limits, declared per routine with defaults: `iterations: 10`,
`minutes: 20`, `tokens: 400000`. Hitting any of them ends the run as
`budget`. The routine's systemd `TimeoutStartSec` sits above `minutes` so
systemd never wins the race.

### 6. Outcome and hand-off

Every run ends in exactly one of:

| outcome | meaning | exit |
|---|---|---|
| `green` | condition passed on the last iteration | 0 |
| `already_green` | condition passed before any edit; nothing to do | 0 |
| `stalled` | same condition output three iterations running | 0 |
| `budget` | iterations, minutes or tokens exhausted | 0 |
| `too_large` | diff cap reached | 0 |
| `error` | could not clone, condition command missing, API down | 1 |

Only `error` is a failure — the others are findings, the same rule the
sweep follows. For every outcome but `error` and `already_green`, the run
leaves:

- branch `goal/<name>` in the routine's checkout, with one commit per
  iteration that changed something (the transcript explains each), squashed
  on hand-off to one commit whose message carries the condition, the
  outcome, and the iteration count;
- `~/jarvis-data/routines/<name>/propagation/<name>-<stamp>.patch`;
- `runs/<stamp>/transcript.jsonl` — every model call and tool call, so a
  person can see how it got there;
- one `agent` event (`kind: goal`, severity `notice` for green, `warning`
  otherwise) with the outcome, iterations, files touched and the patch path.

The run **stops before push**, like loop 2. A person lands it with `git am`
and the repository's `ship.sh`. A non-green branch is still handed off:
partial progress with a transcript is worth more to a human than nothing.

### 7. Registry shape

```yaml
  atlas-mypy-green:
    description: Bring packages/atlas-core to mypy --strict clean.
    schedule: "Sat 06:00:00"
    kind: goal
    repo: https://github.com/barelysmash/atlas
    base: main
    setup:                       # once per fresh checkout, not per run
      - python -m pip install -e packages/atlas-core[dev]
    condition: python -m mypy --strict packages/atlas-core/src
    commands:                    # what run(name) may invoke
      mypy: python -m mypy --strict packages/atlas-core/src
      tests: python -m pytest -q packages/atlas-core
    paths:
      - packages/atlas-core/src/**
    budget: {iterations: 10, minutes: 20, tokens: 400000}
    max_changed_lines: 400
    timeout_minutes: 25
```

`orchestrator/routines.py` validates this shape and `gen_routine_units.py`
renders the same unit pair it does for `kind: shell`; the unit's `ExecStart`
is still `run_routine.py <name>`, which dispatches on `kind`.

### 8. One at a time

A lock file per target repository URL under the routines state dir. A goal
run that finds the lock held exits `error` with a clear message rather than
racing another run on the same repository.

## Consequences

Positive:

- a green result cannot be manufactured by editing the test, the config or
  the condition, because those are not writable;
- the shared brain and Barry's memory are untouched by construction, not by
  convention;
- every run is reviewable after the fact from its transcript and patch;
- the hand-off boundary, event shape, patch location and `ship.sh` landing
  step are the same as loop 2, so there is one way to land bot-prepared
  work;
- loop 4 (backlog drain) becomes "pick an item, write a goal entry, run
  it" with no new machinery.

Negative:

- the proposer cannot install packages or run arbitrary commands, so a goal
  whose fix needs a new dependency ends `stalled` and needs a person;
- declaring `commands:` and `paths:` per routine is more YAML than a free
  agent would need; that is the price of the rails;
- three-iteration stall detection can stop a run that was about to converge;
  the transcript shows that, and the budget can be raised per routine;
- the proposer's model cost is real and recurring. The token budget bounds
  it per run; the schedule bounds it per week.

## Not decided here

- Which repository gets the first goal routine and what its condition is.
  Atlas `mypy --strict` on one package is the obvious candidate; the choice
  is made in the registry entry, not this ADR.
- Whether a goal run should open a PR when `--push` is eventually granted
  to the sweep. Same decision as loop 2's; this ADR inherits whatever is
  decided there.
- Loop 4's item selection from `TASKS.md`. Out of scope.
