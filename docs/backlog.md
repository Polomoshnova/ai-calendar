# Backlog API

The internal Backlog API exposes explicit Planner backlog operations while
preserving the domain boundary. All routes require internal tools to be enabled.
They do not invoke scheduling, contact a calendar provider, or create backlog
entries automatically.

## Endpoints

- `GET /internal/api/backlog?user_id=...` lists active and deferred entries by
  default. Optional `status`, `reason`, `origin`, `due_only`, `limit`, and
  `offset` filters are supported.
- `GET /internal/api/backlog/{entry_id}?user_id=...` returns one owned entry.
- `POST /internal/api/backlog` creates an explicit entry. If
  `remaining_duration_minutes` is omitted, the application subtracts sessions
  in reserving SchedulePlans from `Task.duration_minutes`.
- `POST /internal/api/backlog/{entry_id}/defer?user_id=...` moves an active
  entry to deferred and requires `deferred_until` or `next_review_at`.
- `POST /internal/api/backlog/{entry_id}/reactivate?user_id=...` performs only
  the deferred-to-active transition.
- `POST /internal/api/backlog/{entry_id}/resolve?user_id=...` resolves the
  backlog condition.
- `POST /internal/api/backlog/{entry_id}/cancel?user_id=...` explicitly stops
  backlog tracking.

List ordering is deterministic: review time first, followed by `entered_at`
and entry ID. Ownership failures return 404, conflicting open entries and
invalid lifecycle transitions return 409, and request or domain validation
failures return 422. Equivalent repeated creation returns the existing open
entry.

`origin` and `reason` use the domain enums. `calendar_unavailable` is not a
reason; temporary provider failures remain integration errors. `reason=other`
requires a meaningful note.

The generated OpenAPI document includes typed request and response schemas,
operation summaries, boundary descriptions, and creation/defer examples.
