# ADR-0003: Backlog drain (loop 4)

Status: Accepted
Date: 2026-10-25 (built the same day: `scripts/routines/backlog_drain.py`)

## Context

Loops 1–3 are live (`docs/LOOPS.md`). The goal runner (ADR-0002) turns a
registry entry — repo, condition command, allowed paths, budget — into a
branch and a patch, unattended. Each goal is still a hand-written entry in
`config/routines.yaml` with its own timer: `jarvis-memory-mypy` went green
on its first run and now fires weekly to report `already_green`;
`jarvis-brain-mypy` has its own Saturday slot. Every new goal is another
timer, another stagger, and an entry that outlives its purpose.

Loop 4 was sketched as "pick the next unblocked `TASKS.md` item, write a
goal entry, run it". ADR-0002 §Consequences promised that this would need
no new machinery: `run_goal()` takes a `Routine` and does the rest.

What would go wrong if it were built casually:

- a `TASKS.md` item is prose. The only evaluator the goal runner has is a
  command's exit code (ADR-0002 §1). Asking a model to invent a condition
  from "Rename calendar.py → gcal.py" is model-as-judge through the back
  door: whoever writes the condition decides what green means;
- a queue that re-runs a green item every fire burns a clone, a venv and
  a model call per week to rediscover a patch nobody has landed yet;
- a queue that re-runs a stalled item every fire burns the budget on the
  same wall until someone edits the item;
- two goals on one repo at once collide on the per-repo lock and one of
  them is an `error` run (ADR-0002 §8).

## Decision

### 1. The backlog is `config/backlog.yaml`; `TASKS.md` stays prose

A drainable item is a goal spec, not a sentence. `config/backlog.yaml` is
an ordered list of entries with exactly the `kind: goal` fields of a
routine (`repo`, `base`, `condition`, `commands`, `paths`, `setup`,
`unprotect`, `budget`, `max_changed_lines`, `model`) minus `schedule`,
`timeout_minutes` and `jitter_seconds`, which belong to the drain. Two
fields are new:

- `task:` — the `TASKS.md` checkbox line this entry fulfils, verbatim
  text after `- [ ] `. Optional. Used only at hand-off (§4);
- `after:` — names of entries that must be **landed** (§3) before this
  one is eligible. Optional.

Opting an item in means writing its condition. That is the one piece of
thinking a person must do, and it is done once, in a reviewed commit, not
by a model at 06:00. `TASKS.md` is never parsed. An item with no entry is
not drainable, and that is the correct state for most items.

The registry loader validates `backlog.yaml` with the same `_parse_goal`
as a routine's goal block, so a malformed entry fails CI, not a Sunday.

### 2. The drain is one `kind: shell` routine; one entry per fire

`backlog-drain` is an ordinary entry in `routines.yaml`
(`scripts/routines/backlog_drain.py`, Sun 06:00 — goal routines own
Saturday). Each fire:

1. loads `backlog.yaml`, reads `queue.json` from its own state dir;
2. picks **one** entry by §3;
3. builds a `Routine(name="backlog-<entry>", kind="goal", goal=<spec>)`
   and calls `run_goal()` in-process with state
   `~/jarvis-data/routines/backlog-<entry>/` — a sibling of the drain's
   own state dir with the same `venv/`, `repo/`, `runs/`, `propagation/`
   layout a goal routine has, so the goal runner's per-repo lock
   (`routines/.locks/`) is the one the standalone routines use;
4. records the outcome, base sha and spec hash in `queue.json`;
5. prints one summary line: `backlog: <entry> → <outcome> [<patch>]`, or
   `backlog: nothing eligible (N landed, M awaiting landing, K parked)`.

One entry per fire keeps the drain's `timeout_minutes` meaningful (it is
the entry's `budget.minutes` plus clone and setup, not a sum over the
queue) and keeps Sunday's model spend bounded by one budget. A second
entry waits a week; if that is too slow, the fix is a second drain timer
with a different `--only`, not a loop inside one fire.

### 3. Entry states, and what makes one eligible

Per entry, `queue.json` holds `state`, `base` (sha the last run started
from), `spec` (hash of the entry's YAML), `last_run`, `patch`.

| state | meaning | re-eligible when |
|---|---|---|
| `pending` | never run, or reset by hand (delete its record) | always |
| `green` | last run produced a patch; awaiting a person to land it | the repo's `base` has moved since the run (the patch may be stale; rebuild it — same rule as propagation's nightly rebuild) |
| `landed` | a run came back `already_green`: the condition holds on `base` without a patch | never. A landed entry is skipped, and listed in the summary so it can be deleted from `backlog.yaml` |
| `parked` | `stalled`, `budget`, `too_large` or `error`: the runner could not get there; a person has to look at the transcript | the entry's spec hash changes (someone edited the condition, the paths, the budget) |

Eligibility, in file order: the first entry whose state allows a run and
whose `after:` entries are all `landed`. "Landed" is detected by the goal
runner itself — `already_green` on a fresh clone of `base` — so there is no
second source of truth and no parsing of merged PRs. The cost is one clone
and one condition run per `green` entry each time `base` moves; that is
the same cost the standalone routines pay weekly, and it stops the moment
the entry is `landed`.

An entry that is `green` and whose base has not moved is skipped: the
patch on disk is still correct, and re-running it would only re-spend the
model to produce the same diff.

### 4. Hand-off ticks the task

The goal runner's hand-off is unchanged: one squashed commit on
`goal/backlog-<entry>`, a `.patch`, a transcript, HEAD detached. After a
`green` run the **drain** — never the proposer — makes one addition in the
checkout: if the entry has `task:` and `TASKS.md` on that branch contains
`- [ ] <task>` exactly once, it becomes `- [x] <task> — DONE <date>
(backlog-drain)`, the commit is amended, and the patch is regenerated. If
the line is not found, or found more than once, nothing is ticked and the
summary says so. The proposer cannot write `TASKS.md` (it is outside
`paths:`), so a ticked box is always the drain's, always after green.

Landing is unchanged from loops 2 and 3: `scp` the patch, `git am`, the
repo's `ship.sh` or the PR steps in `docs/ROUTINES.md`. The drain never
pushes.

### 5. Rails and stop points

- The proposer's rails are ADR-0002's, untouched: the drain only builds
  the `Routine`; protection, `paths:`, budgets, stall, the diff cap and
  the lock all apply as before.
- The per-repo lock is shared with the standalone goal routines. Sunday
  is the drain's day so that it does not race Saturday's goals; if a
  standalone routine ever moves to Sunday, it staggers around the drain.
- `--probe <entry>` and `--only <entry>` exist for a person: probe
  behaves exactly as the goal runner's probe; `--only` runs that entry
  regardless of state (a forced retry after reading a transcript).
- Stop point: same as every loop — anything that pushes, opens a PR,
  merges, or edits `main`.

### 6. Events

The goal run already emits its `agent` event (`kind: goal`) with the
outcome and patch path. The drain adds nothing but the `job` event every
routine gets from `run_routine.py`; its summary line (§2) is the cockpit's
one-liner. The runner's events are named `goal:backlog-<entry>`, so the
cockpit shows each backlog item under its own name, not under the drain.

## Consequences

Positive:

- adding a goal is one YAML list item, not a routine, a timer and a
  stagger; retiring one is deleting the item once it reports `landed`;
- `jarvis-memory-mypy` and `jarvis-brain-mypy` can move into the backlog
  once brain is green, leaving `routines.yaml` with the sweep and the
  drain — two timers for all of loops 1–4;
- the drain never runs the model to rediscover a patch it already has,
  and never re-runs a wall it already hit; a person's edit is the only
  thing that re-arms a parked entry;
- the whole of loop 4 is a scheduler plus a state file. `run_goal()` is
  called, not changed.

Negative:

- one entry per Sunday is slow for a long backlog. Deliberate: it is the
  spend bound. A second drain timer is the escape hatch;
- "landed" costs a clone and a condition run per `green` entry per base
  move. For a busy repo that is every Sunday until someone lands the
  patch — which is the point: an unlanded patch is the thing a person
  should notice;
- `after:` is only as good as the entry names; a typo is caught by the
  loader (unknown name → config error), a wrong dependency is not;
- `TASKS.md` and `backlog.yaml` can disagree (an item ticked by hand,
  an entry nobody deleted). The summary's `landed` list is the nudge;
  nothing enforces it.

## Not decided here

- Whether the standalone goal routines are retired into the backlog, and
  when. After `jarvis-brain-mypy`'s first real run, on its evidence.
- Priority beyond file order. If it is ever needed it is a `priority:`
  field on the entry, not a model's ranking.
- Anything that would let the drain *create* entries from `TASKS.md`.
  Rejected by §1, not deferred.
- `--push`. Inherited from loops 2 and 3, as before.
