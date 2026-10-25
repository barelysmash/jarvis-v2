# Automation loops

How JARVIS processes work through the platform's repositories without a
person in every cycle. The four loop primitives — turn-based, goal-based,
time-based, proactive — each map to one loop below. `docs/ROUTINES.md`
covers the mechanism the time-based loop runs on; this page is the plan.

| # | Loop | Primitive | Status |
|---|------|-----------|--------|
| 1 | **Conformance sweep** — clone every conforming repo, run `jam validate --json`, diff against the previous sweep, emit drift to the cockpit. Read-only. | time-based (`config/routines.yaml` → systemd timer on guildenstern) | **Live.** Routine `conformance-sweep`, weekdays 05:30. |
| 2 | **Contract propagation** — a new JAM release tag fans out an adoption branch (`jam.yaml` pin, re-vendored schemas, `VERSION` note) to every conforming repo that is behind, and stops before push. Landing it is `git am` + the repo's `ship.sh`. | polled from the sweep (the tag list is already in hand; no new ingress) | **Live**, stop-before-push. `--push` exists but is off until the sweep is given write credentials. |
| 3 | **Run-to-green** — iterate on one repo (edit, run the condition, read the failure, edit) until the condition command exits 0 or a budget runs out, then hand off a branch. | goal-based (`kind: goal` in `routines.yaml`) | **Live** per `docs/adr/ADR-0002-goal-runner.md`: `orchestrator/goal_runner.py`. `jarvis-memory-mypy` went green on its first scheduled run (PR #22); `jarvis-brain-mypy` is the second, with a two-part condition via `scripts/goal_check.py`. |
| 4 | **Backlog drain** — on a schedule, pick the next unblocked TASKS.md item and spawn a run-to-green for it. | time-based → goal-based | **Designed**: `docs/adr/ADR-0003-backlog-drain.md` (Proposed). `config/backlog.yaml` of goal specs, one `backlog-drain` routine, one entry per fire, `TASKS.md` stays prose. |

## Rules every loop follows

- **Loops consume JAM; they never change it.** `jam validate`, the
  schemas and the category registry are inputs. Proposing changes to JAM
  is the jam-sdk agent's work.
- **Writes go through the existing guards.** Any loop that touches a
  repository calls that repository's `apply-lib.sh` and `ship.sh`
  (scratch-tree verification, never commit to main, poll CI to
  `completed`) rather than reimplementing them. Those guards exist
  because the failures they prevent already happened by hand.
- **Unattended runs cannot ask.** Each loop declares where it stops and
  hands off: anything that would merge, delete, or land on `main`.
- **Isolation.** `kind: shell` routines are subprocesses with no brain
  and no HUD session. A `kind: goal` routine must start a fresh,
  isolated brain session per run — never the shared singleton.
- **Findings are not failures.** A repo at 10/11 is a report line and a
  cockpit event; exit non-zero means the loop itself could not do its
  job.

## Order

1 → 2 → 3 → 4. The sweep is read-only and gives the others a baseline to
trigger from. Propagation is the real cross-repo pattern; it prepares the
change and leaves push, PR and merge to `ship.sh`. Run-to-green has the
highest payoff and the largest blast radius, so it goes third, once there
is a repo with a clear green condition to chase. The drain is a scheduler
over the rest.
