# JARVIS Routines — the time-based loop

A routine is a scheduled, unattended job. The briefing and the sleep cycle
were the first two; they were wired by hand as systemd units. Routines put
that pattern behind a registry so the next one is a YAML entry, not a pair
of unit files and a deploy-config edit.

    config/routines.yaml  ──gen_routine_units.py──▶  deploy/systemd/jarvis-routine-*.{service,timer}
                                                             │
                                                     remote_install.sh enables every timer
                                                             │
                                             timer ──▶ scripts/run_routine.py <name>
                                                             │
                                                 event_log.job(<name>) ──▶ cockpit
                                                             │
                                                   the routine's own script

## Adding a routine

1. Write the script. It runs from the repo root with the jarvis venv's
   Python, gets `JARVIS_ROUTINE` and `JARVIS_ROUTINE_STATE` in its
   environment, and must treat the state directory as the only place it
   writes. Findings go in its report and exit 0; exit non-zero only when
   it could not do its job. Its last non-empty stdout line is the summary
   the cockpit shows, so make that line count.
2. Declare it in `config/routines.yaml` (fields are documented in the file).
3. `py scripts/gen_routine_units.py` and commit the generated units. CI
   runs `--check`, so a registry edit without a regenerate fails the build.
4. Deploy. Both deploy paths run `scripts/install_routine_units.sh` on
   guildenstern (`./deploy.sh` after its pull, `deploy/remote_install.sh`
   after the release swap): units are copied into `~/.config/systemd/user/`,
   timers enabled or disabled per the registry, and timers whose entry was
   removed are retired. The cockpit's Scheduled pane picks it up with no
   further wiring, including **run now**.

To try one before its timer fires:

    py scripts/run_routine.py conformance-sweep --dry-run    # shows the command
    py scripts/run_routine.py conformance-sweep              # runs it, logs, emits events

Or on guildenstern: `systemctl --user start jarvis-routine-conformance-sweep.service`
and watch `~/jarvis-data/logs/routine-conformance-sweep.log`.

## What the runner guarantees

- **Isolation.** `kind: shell` routines are subprocesses. They never touch
  the brain or the shared HUD session (the "session isolation" rule from
  TASKS.md holds trivially: there is no session). `kind: goal` routines run
  the GoalRunner's own minimal tool loop with a fresh model conversation per
  run — never JarvisBrain and never the shared session (see below).
- **A hard timeout.** `timeout_minutes` kills the script; the unit's
  `TimeoutStartSec` is one minute longer so systemd never wins that race.
- **A record.** `$JARVIS_DATA_DIR/routines/<name>/runs/<stamp>.log` keeps
  the last 30 runs' output; `last.json` has the outcome. All of it lives
  under `jarvis-data`, outside the release, so deploys and rollbacks never
  touch it.
- **Visibility.** `job` start/end events with duration, exit status and
  summary. Anything the routine itself emits (see below) lands beside them.

## Routine: `conformance-sweep`

The first routine, and the read-only half of the cross-project loop: for
every conforming repo, clone it, run the JAM SDK's `jam validate --json`,
diff the result against the previous sweep, and report. It consumes JAM;
it never proposes changes to JAM or to the repos.

- **jam-sdk** is installed into a private venv under the routine's state
  dir from `--jam-source` at `--jam-ref` (default `main`), and reinstalled
  only when that ref resolves to a new commit. Pin a tag to freeze it.
- **Repos** come from `--repo name=url[@branch]`. Public repos need no
  credentials. A private repo needs read access for `ocelia` on
  guildenstern (a deploy key or credential helper), the same way the
  existing GitHub deploy key gives it `jarvis-v2`.
- **Releases**: see below.
- **Drift** is anything that changed between sweeps: a check that went
  green→red (`regression`, severity `warning`), red→green, or a check the
  SDK added or dropped (`drift`, severity `notice`). One `agent` event per
  changed repo, with the failing check names in `data`, so the cockpit's
  Agents & changes pane and the HUD see it. An unchanged repo emits nothing.
- **Output**: `report.json` (this run), `history/<repo>/latest.json` (what
  the next run diffs against), and a one-line summary such as
  `atlas 10/11 (=) · jarvis-v2 11/11 (△) · jam@8382df0`.

Exit 1 means the sweep could not run — jam-sdk would not install, a repo
would not clone, `jam` crashed. A repo at 10/11 is a finding, not a failure.

### Releases and propagation (loop 2)

The same run watches JAM for release tags (`vX.Y.Z`; the newest wins) and
compares each repo's `jam.yaml` `manual_version` to it. State lives in
`jam-release.json`; a new tag emits one `release` event, a repo falling
behind emits one `behind` event — not one per night.

With `--propagate` (on, in `routines.yaml`), a repo that is behind gets a
branch prepared **in the sweep's own checkout** under
`~/jarvis-data/routines/conformance-sweep/repos/<repo>/`:

- `jam.yaml`: `manual_version` bumped (quoting preserved);
- every `<name>.schema.json` in the repo that is byte-identical to JAM's at
  the *old* pin is replaced with JAM's at the new tag — identical content is
  the only proof it was vendored rather than written. A schema file that
  differs is left alone and listed in the commit message for a human;
- a `VERSION` note beside vendored schemas (`jam vX.Y.Z`, `<name> X.Y.Z`
  lines) is rewritten;
- one commit on `chore/jam-vX.Y.Z`, authored `JARVIS conformance-sweep`,
  and the same commit as `propagation/<repo>-jam-vX.Y.Z.patch`.

The branch is rebuilt from the repo's current tip every night while it is
behind, so it never goes stale, and is deleted once the repo's pin catches
up. One `propagation` event fires when the prepared *content* changes (the
tree, not the commit — timestamps don't count), with the patch path in the
summary.

**That is where an unattended run stops.** guildenstern's GitHub key is
read-only, so landing the change is a person's step, from Rosencrantz:

    scp ocelia@guildenstern:jarvis-data/routines/conformance-sweep/propagation/atlas-jam-v1.4.0.patch .
    cd /c/bSmash-dev/atlas && git checkout -b chore/jam-v1.4.0 main && git am atlas-jam-v1.4.0.patch
    ./scripts/ship.sh chore/jam-v1.4.0

`ship.sh` does the push, PR, check-polling and merge it always has. CI is
the gate: a schema version bump usually also needs the code that declares
versions (`SCHEMA_VERSIONS` in Atlas) updated, and the contract tests will
say so.

`--push` goes one step further — pushes the branch and opens the PR from
guildenstern — and needs a pushable remote plus an authenticated `gh` for
`ocelia`. It is deliberately not in `routines.yaml`: turning it on is a
decision to give an unattended process write access to the repos. Merging
is never done by the sweep under any flag.

## Routine kind: goal (loop 3)

A `kind: goal` routine is run-to-green (`docs/adr/ADR-0002-goal-runner.md`):
`orchestrator/goal_runner.py` iterates on a repository until the routine's
`condition` command exits 0, or a budget runs out, then hands off a branch
and a patch the same way the sweep does. The registry entry declares the
repo, the condition, the named `commands:` the proposer may run, the
`paths:` it may write, optional `setup:` commands, budgets, and a diff cap;
`routines.yaml` has the field notes; `jarvis-memory-mypy` was the first
(green on its first scheduled run, landed as PR #22) and `jarvis-brain-mypy`
the second.

**A condition with more than one part.** `condition` is one command. When
green has to mean two things — `jarvis-brain-mypy` needs the package
strict-clean *and* the brain tests still passing, because those tests fake
SDK content blocks with `SimpleNamespace` and `isinstance` narrowing would
satisfy mypy while breaking them — that one command is
`scripts/goal_check.py -- <cmd> -- <cmd> ...`: each part runs in order under
a header, the first failure stops it and its exit code is the result. The
script sits in `scripts/` and is named by the condition, so the proposer
cannot edit it. Point the condition at the *package* (`orchestrator`), not
the file you want fixed: a file named in the condition is protected.

What a run leaves under `~/jarvis-data/routines/<name>/`:

- `venv/` — a private interpreter. `setup`, the condition and every named
  command run with it first on PATH, so a routine can `pip install` freely
  without touching the production `jarvis-venv`;
- `repo/` — the checkout, HEAD detached between runs; the work is on
  branch `goal/<name>`, squashed to one commit on hand-off;
- `runs/<stamp>/transcript.jsonl` and `result.json` — every model and tool
  call, and the outcome;
- `propagation/<name>-<stamp>.patch` — when anything was changed.

Outcomes: `green`, `already_green`, `stalled`, `budget`, `too_large`
(exit 0, findings) and `error` (exit 1). One `agent` event (`kind: goal`)
carries the outcome and the patch path to the cockpit.

Goal routines on the same repo share one lock; stagger their schedules
(`jarvis-memory-mypy` 06:00, `jarvis-brain-mypy` 07:00) so the second does
not simply find the lock held and exit.

Before scheduling a new goal routine, probe it — clone, setup, condition,
no model — and read what the proposer would be chasing:

    ssh ocelia@guildenstern 'cd ~/jarvis && JARVIS_DATA_DIR=~/jarvis-data \
      ~/jarvis-venv/bin/python orchestrator/goal_runner.py jarvis-memory-mypy --probe'

The proposer needs `ANTHROPIC_API_KEY`, which the unit already has from
`jarvis.env`. `JARVIS_GOAL_MODEL` overrides the default model; a routine's
`model:` overrides that.

Landing a goal branch is the same as a propagation branch: `scp` the patch,
`git am`, the repo's `ship.sh`. A non-green run still leaves a branch and a
transcript; read the transcript before deciding whether the partial work is
worth keeping.

## What comes next (not built)

4. **Backlog drain**: one `backlog-drain` routine that picks the next
   eligible entry of `config/backlog.yaml` (a list of goal specs) and runs
   it through the goal runner, one per fire. Designed in
   `docs/adr/ADR-0003-backlog-drain.md`; see `docs/LOOPS.md`.
