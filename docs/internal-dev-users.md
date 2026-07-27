# Internal development users

`POST /internal/api/dev/users` is a local-development get-or-create helper for
integration smoke tests, internal tooling, and Google Calendar OAuth testing. It
is available only when `ENABLE_INTERNAL_TOOLS=true` and returns `404` otherwise.
It is not a registration, login, or production onboarding endpoint and must
never be exposed as one.

Request:

```json
{
  "email": "polomoshnova812@gmail.com",
  "timezone": "Europe/Warsaw"
}
```

Response:

```json
{
  "id": "7cdd1c68-3a2d-4dcb-b7e9-1d18a4d8f6af",
  "email": "polomoshnova812@gmail.com",
  "timezone": "Europe/Warsaw",
  "created_at": "2026-07-27T09:30:00Z",
  "updated_at": "2026-07-27T09:30:00Z",
  "created": true
}
```

The endpoint trims and lowercases email addresses and validates the timezone as
an IANA timezone. Repeating the request, including with different email casing,
returns the same user with `"created": false`. If the user already exists, the
stored timezone is returned unchanged; this endpoint has no update semantics.

The returned UUID can be passed to Google Calendar OAuth start. OAuth continues
to bind connections by `user_id`, not by email.
