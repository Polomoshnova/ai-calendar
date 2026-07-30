# Product decisions

Last verified against code: 2026-07-28

Latest verified Alembic revision: `20260728_07`

These decisions define intended product behavior. “Foundation implemented”
means types, persistence, or pure policy exist without a runtime workflow.

| Decision | Rationale | Trade-off | Current status |
|---|---|---|---|
| After a corresponding event is successfully created, Google owns actual start, actual end, calendar placement, and event existence. | User edits in the calendar must remain authoritative. | Local planning state can become inconsistent and requires reconciliation. | Accepted; apply and reconciliation are planned. Runtime Google access is read-only. |
| Before apply, SchedulePlan and ScheduledSession remain application-owned. | A confirmed proposal must be durable before any provider event exists. | Confirmation and provider application are separate lifecycle stages. | Implemented. |
| The application owns Task identity, title, priority, structure, planning metadata, and planning history. | Calendar events are execution artifacts, not the complete task model. | Reconciliation must preserve separate ownership boundaries. | Implemented for existing Task fields and SchedulePlan history. A category field does not currently exist. |
| `ScheduledSession` is the synchronization unit, and `CalendarEventMapping` is the sole external identity and sync-state source. | Split tasks need independent provider events without polluting the planning entity. | Mapping persistence and partial apply behavior are required. | Foundation implemented; runtime mapping creation is planned. |
| One task may have sessions in different Google accounts and calendars. | Work may span personal, team, or dedicated calendars. | Per-session credentials, ownership checks, and partial failures become more complex. | Snapshot and mapping shapes are per connection/session. Current DB uniqueness permits one connection per user/provider, so multi-account Google writes remain planned. |
| Busy sources and write targets are separate concepts. | Calendars considered for conflicts need not be the calendars receiving new events. | Both selections must be captured and hashed. | Implemented for new SchedulePlans; nullable snapshots preserve legacy compatibility. |
| A user may choose a dedicated calendar for application-created tasks. | Separating generated work can improve visibility and control. | The chosen calendar may disappear or lose write access. | Calendar selection exists for availability; a dedicated write-target setting and apply UI are planned. |
| Moving a session past the current deadline extends the deadline to the latest explicitly positioned, non-deleted session end. | User placement should not immediately make the task invalid. | Deadlines can move later without a separate reschedule. | Pure `deadline_after_external_move()` policy implemented; no pull workflow applies it. |
| After an external move, check all task sessions for consistency and never automatically move the user's event back. | The calendar edit is authoritative after apply. | The system may report an inconsistent task that needs user action. | Pure checker implemented; runtime invocation is planned. |
| External deletion does not automatically return a task or session to backlog. | Deletion may be intentional and backlog recreation would be surprising. | Recovery requires explicit product behavior. | Accepted and represented by issue/status enums; backlog does not exist. |
| Confirmed but unapplied plans reserve time. | Other scheduling runs should not reuse user-approved blocks. | Reservations can become stale and require obsoletion/revalidation. | Database-backed preview and revalidation merge reserved intervals into busy time; revalidation excludes its current plan. |
| Synchronization is pull-first. | Pull provides a deterministic reconciliation authority independent of delivery reliability. | Changes are not immediate without polling or a trigger. | Accepted; pull runtime is planned. |
| Push notifications may later trigger pull reconciliation but are not authoritative. | Push can reduce detection latency without changing ownership logic. | Webhook infrastructure adds operational complexity. | Later; no webhook or push implementation. |

## Consistency behavior

The checker evaluates all supplied non-deleted sessions for chronological order,
overlap, minimum breaks, and deadline. An externally deleted session produces a
separate issue. It returns diagnostics only: it does not reschedule, change a
Task, call Google, or recreate backlog work.
