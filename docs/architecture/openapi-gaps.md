# OpenAPI documentation gaps

Last verified against code: 2026-07-28

Latest verified Alembic revision: `20260731_10`

## Review result

FastAPI registers public Task and scheduling routes plus internal workflow,
SchedulePlan, revalidation, calendar, and development-user routers. Response
models are present for the reviewed operations, and no API schema exposes
encrypted tokens, refresh tokens, OAuth codes, or raw provider payloads.

The following paths were verified from router prefixes and decorators:

- `POST /internal/api/task-drafts/analyze`
- `POST /api/v1/scheduling/preview`
- `POST /internal/api/schedule-plans/from-preview`
- `GET /internal/api/schedule-plans/{plan_id}`
- `GET /internal/api/users/{user_id}/schedule-plans`
- `POST /internal/api/schedule-plans/{plan_id}/confirm`
- `POST /internal/api/schedule-plans/{plan_id}/revalidate`
- `POST /internal/api/schedule-plans/{plan_id}/apply`
- `POST /internal/api/calendar-event-mappings/{mapping_id}/sync`
- `POST /internal/api/external-calendar-changes/{change_id}/process`
- `GET /internal/api/schedule-plans/{plan_id}/revalidations`
- `POST /internal/api/calendar/google/oauth/start`
- `GET /internal/api/calendar/google/oauth/callback`
- `GET /internal/api/calendar/connections/{connection_id}`
- `DELETE /internal/api/calendar/connections/{connection_id}`
- `GET /internal/api/calendar/connections/{connection_id}/calendars`
- `PUT /internal/api/calendar/connections/{connection_id}/selections`
- `POST /internal/api/calendar/connections/{connection_id}/free-busy`
- `POST /internal/api/dev/users`

## Gaps

- Several routes rely on function-name-generated summaries and have no explicit
  operation description or examples.
- AI analysis and task confirmation routers set `include_in_schema=False`, so
  those implemented internal endpoints do not appear in generated OpenAPI.
- The scheduling lab router is intentionally excluded from OpenAPI.
- SchedulePlan response compatibility fields can expose mapping-derived
  external identity when a mapping exists, but the schema does not explain that
  `CalendarEventMapping` is the persistence source.
- Apply exposes a structured internal
  `POST /internal/api/schedule-plans/{plan_id}/apply` response; production
  authentication and public API exposure remain pending.
- Pull synchronization exposes a structured single-mapping result; batch,
  polling, and ConsistencyChecker processing remain intentionally absent.
- Revalidation OpenAPI does not state prominently that
  `include_internal_busy=true` remains provider-only in the current service.
- OAuth and calendar route schemas are safe, but their generated documentation
  does not provide the complete setup and read-only boundary described in
  `../google-calendar-readonly.md`.
- The development-user endpoint is documented by types but not explicitly
  labeled as an internal get-or-create helper rather than registration.

No Python source was changed for this review. Adding summaries, descriptions,
examples, and selective schema visibility requires a separate documentation/API
metadata decision because it affects generated OpenAPI and the exposure of
development-only operations.
