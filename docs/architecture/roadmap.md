# Roadmap

Last verified against code: 2026-07-28

Latest verified Alembic revision: `20260728_07`

This roadmap records dependency order, not delivery dates.

## Implemented

| Capability | Purpose | Dependencies | Non-goals |
|---|---|---|---|
| AI Intake and confirmation | Convert natural language into reviewed `ConfirmedTask` data. | OpenAI structured output, Pydantic contracts. | AI does not choose slots or persist a Task automatically. |
| Deterministic availability, scheduling, and preview | Produce explainable placement and unscheduled outcomes. | Tasks, preferences, normalized busy intervals. | No provider calls inside the scheduler. |
| SchedulePlan persistence and confirmation | Preserve exact, versioned proposals and user approval. | PostgreSQL, SQLAlchemy, Alembic. | Confirmation does not create Google events. |
| Google read-only integration | Use real calendar availability. | OAuth, encrypted credentials, calendar selection, FreeBusy. | No event content or writes. |
| SchedulePlan revalidation | Check immutable sessions against fresh provider busy data. | Confirmed plan, active connection, FreeBusy. | No rescheduling or apply. |
| Reserved interval repository | Define which persisted plans block time. | SchedulePlan statuses and sessions. | Integrated with database-backed preview and revalidation. |
| Calendar synchronization domain foundation | Define mappings, change audits, snapshots, hashing, consistency, and deadline policy. | Revision `20260728_07`. | Apply consumes mappings and snapshots; no polling or runtime reconciliation. |

## Next

### 1. ApplySchedulePlan — implemented

Purpose: perform final readiness validation and explicitly apply a confirmed
plan.

Dependencies: immutable write target snapshots, active connections, and the
minimal Google event write scope.

Non-goals: rescheduling or silently repairing conflicts.

### 2. Mapping persistence during apply — implemented

Purpose: create one `CalendarEventMapping` for every successfully created
Google event.

Dependencies: ApplySchedulePlan, provider idempotency strategy, per-session
write target.

Non-goals: duplicating external identity on `ScheduledSession`.

### 3. Partial apply and idempotent retry — implemented

Purpose: make `partially_applied` and retry transitions operational without
duplicating events.

Dependencies: durable mappings, safe provider error classification, readiness
checks.

Non-goals: best-effort success without an auditable result.

### 4. Pull reconciliation

Purpose: compare mapped events with Google, record changes, run consistency
checking, and apply explicit local policies.

Dependencies: mappings created by apply, provider event reads, change audit
repository, consistency and deadline policies.

Non-goals: moving user-edited events back automatically.

### 5. Basic product UI

Purpose: expose intake, confirmation, preview, plan review, apply, and conflict
states through an authenticated product surface.

Dependencies: production authentication and stable apply contracts.

Non-goals: replacing the internal scheduling lab as a developer diagnostics
tool.

### 6. Backlog domain

Purpose: define explicit handling of unscheduled or intentionally deferred work.

Dependencies: product decisions for deletion, partial apply, and recovery.

Non-goals: automatically returning externally deleted sessions to backlog.

## Later

| Capability | Purpose | Dependencies | Non-goals |
|---|---|---|---|
| Push/webhook-assisted synchronization | Reduce time before the next pull. | Stable production pull reconciliation and provider operations. | Push is not the source of truth. |
| Advanced conflict resolution | Help users resolve inconsistent multi-session tasks. | Reconciliation diagnostics and product UI. | Automatic reversal of Google edits. |
| Notifications | Surface apply and consistency outcomes. | Authenticated users and durable event/audit semantics. | No speculative alert SLA. |
| Additional calendar providers | Reuse provider-neutral boundaries beyond Google. | Stable apply/reconciliation contracts. | Outlook or Apple Calendar is not committed MVP scope. |
