# Google Calendar read-only integration

This internal integration reads availability on demand and passes merged,
provider-neutral busy intervals to the existing deterministic preview. It does
not read event contents and has no event create, update, or delete API.

`CalendarProvider` is the provider-neutral boundary. The scheduler receives only
generic `DateTimeInterval` values and never imports Google code. Connections and
selections are persisted; busy intervals and event data are not.

OAuth state is short-lived, hashed, user-bound, and single-use. Tokens are
Fernet-encrypted with `CALENDAR_TOKEN_ENCRYPTION_KEY`, with no plaintext
fallback. The only scope is
`https://www.googleapis.com/auth/calendar.readonly`.

## Environment

```dotenv
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
GOOGLE_CALENDAR_REDIRECT_URI=http://127.0.0.1:8000/internal/api/calendar/google/oauth/callback
GOOGLE_CALENDAR_SCOPES=https://www.googleapis.com/auth/calendar.readonly
CALENDAR_TOKEN_ENCRYPTION_KEY=
```

Generate a Fernet key without committing it:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Google Cloud setup

1. Create or select a Google Cloud project.
2. Enable Google Calendar API.
3. Configure the OAuth consent screen.
4. Add the developer account as a test user while the app is in testing.
5. Create an OAuth Client ID of type Web application.
6. Add exactly `http://127.0.0.1:8000/internal/api/calendar/google/oauth/callback`.
7. Put the client ID and secret in `.env`; never commit them.
8. Generate and configure the encryption key.
9. Run `alembic upgrade head`.
10. Start the backend.
11. Call the OAuth start endpoint.
12. Open its `authorization_url`.
13. Approve read-only access.
14. Confirm the callback reports a connected account.
15. List calendars.
16. Select calendars.
17. Query FreeBusy.
18. Run the calendar-backed preview.

## Endpoints and behavior

- `POST /internal/api/calendar/google/oauth/start`
- `GET /internal/api/calendar/google/oauth/callback`
- `GET|DELETE /internal/api/calendar/connections/{connection_id}`
- `GET /internal/api/calendar/connections/{connection_id}/calendars`
- `PUT /internal/api/calendar/connections/{connection_id}/selections`
- `POST /internal/api/calendar/connections/{connection_id}/free-busy`
- `POST /internal/api/calendar/connections/{connection_id}/scheduling/preview`

Connection-bound endpoints require `user_id` as a query parameter until an
authenticated-user dependency exists. This prevents one user from addressing
another user's connection.

Only the primary calendar is selected on first sync. Selection updates replace
the selected set. FreeBusy requests are batched deterministically in groups of
50. Network errors, 429, and transient 5xx responses receive at most three
bounded exponential-backoff attempts. A 401 causes one refresh and one provider
retry. Ordinary 400/403 responses are not retried.

Disconnect attempts revocation, retains minimal metadata as `revoked`, clears
tokens, and removes selections. Partial FreeBusy failures still produce a
preview with diagnostics; all-calendar failure stops before scheduling.

Reauthorization is HTTP 409, not found is 404, rate limiting is 429, malformed
provider data is 502, and missing configuration or temporary unavailability is
503. Tokens, codes, secrets, event contents, and full provider payloads are not
returned or logged.

## Limitations

- Internal endpoints and one Google account per user.
- No event content, writes, push notifications, or incremental synchronization.
- On-demand FreeBusy only; intervals are not persisted.
- No production OAuth verification work in this change.
