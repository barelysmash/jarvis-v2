# ADR-0001: Repository Role

Status: Accepted
Date: 2026-08-07

## Context

JARVIS is the orchestration layer of the BarelySmash Intelligence Platform.

It receives user intent, determines which agent or tool owns the requested
work, coordinates execution, and synthesizes the result into a user-facing
response.

Without an explicit boundary, an orchestrator tends to accumulate domain
reasoning simply because it can see every domain. JAM requires that ownership
remain with the engine responsible for that domain.

## Decision

JARVIS is the **orchestrator** of the BarelySmash platform.

> Orchestrates user intent across BarelySmash agents and tools; owns
> interaction, delegation, synthesis, and presentation, not domain decisions.

### JARVIS owns

- **Interaction.** Receiving user intent through CLI, API, voice, and HUD
  surfaces.
- **Intent decomposition.** Determining what work is being requested and what
  capabilities are required to satisfy it.
- **Delegation.** Routing work to the agent, workflow, or tool that owns the
  relevant domain.
- **Coordination.** Managing multi-step execution and combining results from
  multiple capabilities.
- **Synthesis.** Turning returned evidence, observations, insights, and
  decisions into a coherent user-facing response without changing their
  underlying domain meaning.
- **Presentation.** HUD, conversational output, notifications, and other
  user-facing representations.
- **Conversation and personal-agent memory.** State used to maintain continuity
  with the user. This is distinct from domain decision state owned by engines
  or consuming applications.
- **Tool invocation infrastructure.** Registration, invocation, status
  reporting, and error handling for tools exposed to the orchestrator.

### JARVIS does not own

- **Operational intelligence.** Atlas owns operational observation,
  interpretation, and operational Decisions.
- **Market intelligence.** Friday owns market analysis, forecasts, risk gates,
  and trading Decisions.
- **Creative intelligence.** Muse owns creative interpretation, briefs,
  critique, and creative outputs.
- **Shared platform contracts.** JAM defines cross-repository architecture,
  standards, Observation, Insight, Decision, DecisionState, and shared
  decision-category semantics.
- **Domain decisions.** JARVIS may request, receive, explain, compare, and
  surface a Decision. It does not become the source of that Decision.
- **Domain decision lifecycle.** Acceptance, rejection, execution, and other
  consumer-owned DecisionState remain with the appropriate consumer.
- **External systems of record.** Calendar, email, market systems, restaurant
  systems, and other integrations remain authoritative for their own data.

### Cross-repository rule

JARVIS communicates with independent engines through explicit contracts.

A delegated result retains its provenance. JARVIS may synthesize multiple
results for presentation, but synthesis must not silently convert an Atlas,
Friday, or Muse result into a new JARVIS-owned domain Decision.

If JARVIS needs domain reasoning that does not exist, the capability belongs in
the appropriate domain engine or requires a deliberate platform boundary
decision.

## Consequences

Positive:

- domain authority remains explicit;
- JARVIS can grow its orchestration capabilities without becoming a monolith;
- Atlas, Friday, Muse, and future agents remain independently testable and
  deployable;
- user-facing synthesis can evolve independently of domain reasoning;
- provenance remains intact across delegation boundaries.

Negative:

- cross-agent work requires explicit interfaces instead of convenient direct
  coupling;
- JARVIS cannot locally patch missing domain behavior without first deciding
  who owns it;
- contract evolution must be coordinated through JAM.

## Exceptions

**Legacy Python layout.** JARVIS predates JAM and keeps importable packages at
the repository root rather than under `src/`. Moving them during adoption would
combine architecture conformance with a high-risk runtime refactor.
*Temporary; remediation is a dedicated package-layout migration.*

**Legacy Ruff and mypy debt.** JAM adoption measured the existing Python
baseline and records file- and module-specific exemptions in `pyproject.toml`.
The global JAM rule set and strict mypy mode remain enabled, so new code does
not receive blanket exemptions. *Temporary; exemptions are removed as affected
modules are cleaned up.*

**Dependency metadata remains in requirements files.** Runtime dependencies
currently live in `requirements.txt` and `requirements-voice.txt`; the new
`[project]` table exists primarily to establish JAM Python metadata and tooling.
*Temporary; remediation is a dedicated dependency and packaging migration.*

**Runtime test coverage is incomplete.** JAM adoption adds structural package
import tests but does not attempt to retrofit behavioral coverage across the
existing orchestrator, voice, server, and integration code in the same change.
*Temporary; remediation is incremental feature-level coverage.*
