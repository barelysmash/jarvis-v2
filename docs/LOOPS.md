# Automation loops

How JARVIS processes work through the platform's repositories without a
person in every cycle. The four loop primitives — turn-based, goal-based,
time-based, proactive — each map to one loop below. `docs/ROUTINES.md`
covers the mechanism the time-based loop runs on; this page is the plan.

| # | Loop | Primitive | Status |
|---|------|-----------|--------|
| 1 | **Conformance sweep** — clone every conforming repo, run `jam validate --json`, diff against the previous sweep, emit drift to the cockpit. Read-only. | time-based (`config/routines.yaml` → systemd timer on guildenstern) | **Live.** Routine `conformance-sweep`, weekdays 05:30. |
| 2 | **Contract propagation** — a JAM release tag fans out a version-bump branch (pinned `jam` version, re-vendored schemas and category registry) to every conforming repo through that repo's own `ship.sh`, and stops before merge. | proactive (`/api/events`, or a GitHub release event) | Not started. First loop that writes. |
| 3 | **Run-to-green** — iterate on one repo (edit, run, read the failure, edit) until an evaluator says the stated condition holds — `jam validate` 11/11, mypy strict clean — or a budget runs out, then hand off a branch. | goal-based (`kind: goal` in `routines.yaml`, reserved for the GoalRunner) | Not started. Needs the GoalRunner and session isolation from TASKS.md. |
| 4 | **Backlog drain** — on a schedule, pick the next unblocked TASKS.md item and spawn a run-to-green for it. | time-based → goal-based | Not started. This is the cockpit's scheduler; it falls out of 1–3. |

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
trigger from. Propagation is the real cross-repo pattern and mostly
reuses `ship.sh`. Run-to-green has the highest payoff and the largest
blast radius, so it goes third, once there is a repo with a clear green
condition to chase. The drain is a scheduler over the rest.
