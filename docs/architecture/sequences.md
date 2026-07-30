# Sequence diagrams

Last verified against code: 2026-07-28

Latest verified Alembic revision: `20260728_07`

## Task intake and scheduling preview

This internal composed flow is implemented and remains stateless.

```mermaid
sequenceDiagram
    actor User
    participant API as FastAPI workflow route
    participant AI as AI Intake gateway
    participant Confirm as Task confirmation
    participant Scheduler as Deterministic scheduler

    User->>API: Natural-language text + DraftReview + planning context
    API->>AI: Analyze text
    AI-->>API: TaskDraftV2
    API->>Confirm: Apply typed review
    Confirm-->>API: ConfirmedTask + audit
    API->>Scheduler: Normalized task, preferences, busy intervals
    Scheduler-->>API: SchedulePreviewResponse
    API-->>User: Draft, audit, trace, and preview
    Note over API,Scheduler: No database writes and no calendar API calls
```

The public `/api/v1/scheduling/preview` flow skips AI and confirmation: it loads
pending Tasks and effective preferences from PostgreSQL, combines them with
temporary request busy intervals and the user's reserved SchedulePlan
intervals, and calls the same deterministic scheduling orchestration.

## SchedulePlan creation and confirmation

This flow is implemented through separate internal endpoints.

```mermaid
sequenceDiagram
    actor Client
    participant API as SchedulePlan API
    participant Plan as SchedulePlan service
    participant Repo as SchedulePlan repository
    participant DB as PostgreSQL

    Client->>API: POST /schedule-plans/from-preview
    API->>Plan: ConfirmedTask + preview + planning context
    Plan->>Repo: Check idempotency key and latest group version
    Repo->>DB: SELECT plans
    DB-->>Repo: Existing or latest plan
    Plan->>DB: INSERT SchedulePlan + ScheduledSessions
    DB-->>Client: proposed plan
    Client->>API: POST /schedule-plans/{id}/confirm
    API->>Plan: Confirm exact stored plan
    Plan->>DB: UPDATE plan and sessions to confirmed
    Note over Plan,DB: The plan's half-open intervals now reserve time
    DB-->>Client: confirmed plan
```

Reservation is defined by the repository query and plan status. Confirmation
does not call that query, create a separate reservation row, or write Google
events.

## SchedulePlan revalidation

All calls in this sequence are implemented.

```mermaid
sequenceDiagram
    actor Client
    participant API as Revalidation API
    participant Reval as Revalidation service
    participant Connection as CalendarConnection
    participant Google as Google FreeBusy
    participant Reserve as Reserved interval repository
    participant DB as PostgreSQL

    Client->>API: POST /schedule-plans/{id}/revalidate
    API->>Reval: Plan ID, connection ID, options
    Reval->>DB: Load eligible plan and immutable sessions
    Reval->>Connection: Validate owner, active status, and calendar selection
    Reval->>Reserve: Load same-user reservations, excluding current plan
    Reserve-->>Reval: Half-open reserved intervals
    Reval->>Google: Query padded FreeBusy window
    Google-->>Reval: Provider-neutral busy intervals and errors
    Reval->>DB: Lock and verify plan version, timestamp, and sessions hash
    Reval->>Reval: Check provider busy and internal reservations
    Reval->>DB: Persist SchedulePlanRevalidation and update plan status
    Reval-->>API: Result, diagnostics, can_apply, valid_until
    API-->>Client: Read-only validation result
```

## ApplySchedulePlan

```mermaid
sequenceDiagram
    actor Client
    participant Apply as ApplySchedulePlan
    participant Google as Google Calendar write API
    participant DB as PostgreSQL

    Client-->>Apply: Apply confirmed plan
    Apply-->>DB: Validate stored targets and claim applying
    loop Each ScheduledSession
        Apply-->>Google: Create idempotent event
        Google-->>Apply: Event ID, calendar ID, etag, updated time
        Apply-->>DB: Insert CalendarEventMapping and commit
    end
    Apply-->>DB: Mark plan applied, partially_applied, or failed
    Apply-->>Client: Apply result
```

Provider calls continue after individual session failures. Existing mappings
are skipped on retry.

## Pull reconciliation — planned

```mermaid
sequenceDiagram
    participant App as Pull reconciliation job
    participant Google as Google Calendar read API
    database DB as PostgreSQL
    participant Checker as ConsistencyChecker

    App-->>DB: Load CalendarEventMappings
    App-->>Google: Read mapped external events
    Google-->>App: Current time, calendar, etag, or deletion
    App-->>DB: Compare mapping and record ExternalCalendarChange
    App-->>Checker: Check all non-deleted task sessions
    Checker-->>App: ConsistencyResult
    App-->>DB: Update mapping/local synchronization state
    Note over App,DB: Deadline policy may extend Task deadline after a move
```

Every arrow is planned. Reconciliation must not automatically move a user's
Google event back or return a deleted session to backlog.
