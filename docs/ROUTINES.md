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
  TASKS.md holds trivially: there is no session). `kind: goal` — a fresh,
  isolated brain session driven by the GoalRunner — is reserved in the
  loader and rejected until it exists, so a typo cannot schedule nothing.
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

## What comes next (not built)

The sweep is loop 1 of four. In order:

2. **Contract propagation** (proactive): a JAM release tag fans out
   version-bump branches to every conforming repo via each repo's
   `ship.sh`, stopping before merge.
3. **Run-to-green** (goal-based): `kind: goal` routines that iterate on
   one repo until an evaluator says the stated condition holds.
4. **Backlog drain**: a scheduled routine that picks the next unblocked
   TASKS.md item and spawns a run-to-green for it — the cockpit's scheduler.

Each needs the write guards the apply/ship scripts already enforce
(scratch-tree verification, never-on-main, CI polled to completion) carried
over unchanged, which is why the read-only sweep goes first.
