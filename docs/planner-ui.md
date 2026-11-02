# Planner UI

`GET /internal/planner` is the first screen in the UI epic. It is an
intentionally small, read-only frontend for the existing Planner projection.
Like the scheduling lab, it is development tooling gated by
`ENABLE_INTERNAL_TOOLS`; it is not a production-authenticated product surface.

## Architecture choice

The repository already served a Jinja template and vanilla JavaScript for the
internal scheduling lab. The Planner UI extends that foundation with a Jinja
page shell, static CSS, and small native JavaScript modules. FastAPI serves all
assets, and there is no bundler or frontend runtime dependency.

A React/Vite client was considered and rejected for this foundation PR. It
would provide a larger component and testing ecosystem, but would also add a
second dependency/build/deployment toolchain before one read-only screen needs
it. This decision can be revisited when later UI work introduces sufficiently
complex shared interactions.

The frontend modules have explicit responsibilities:

- `planner_api.js` owns Planner requests, error translation, and minimum
  response-shape validation;
- `planner_view.js` contains pure presentation functions;
- `planner.js` owns user selection and loading/error/content transitions.

The JavaScript tests use Node's built-in test runner. There are no npm package
dependencies and no frontend build output to deploy.

## Boundary

```text
Planner page
  → GET /internal/api/planner?user_id=...&days=14
  → PlannerReadService
  → persisted domain state
```

The browser does not query persistence, import repositories, invoke the
scheduler, contact a calendar provider, or reconstruct Planner lifecycle
rules. It renders the backend's `today`, `upcoming`, `backlog`,
`needs_attention`, `proposed_plans`, and `confirmed_not_applied` collections.
Current session timestamps come directly from the projection and are formatted
in its `timezone` for display.

The only other request is `GET /internal/api/users`, reusing the scheduling
lab's temporary development-user convention. The selected `user_id` is kept in
the page query string so the view is linkable and reloadable. This is not an
authentication mechanism and must be replaced by authenticated identity before
a production UI is exposed.

There are no product-state mutation requests or action controls in this PR.

## Local development

Start PostgreSQL, apply migrations, and run FastAPI with internal tools enabled
as described in the main README, then open:

<http://127.0.0.1:8000/internal/planner>

The page selects the first development user by default. Choose another user
from the selector or provide `?user_id=<uuid>`. The upcoming horizon is the
Planner API default of 14 days and is displayed but is not configurable here.

Run the UI checks separately from the Python gates:

```bash
npm test
npm run lint
npm run typecheck
npm run build
```

The last three commands perform dependency-free JavaScript syntax validation;
`build` intentionally emits no artifacts.

## UI epic roadmap

This foundation includes only Planner read states: loading, request error,
overall empty, partially populated, and populated. Follow-up PRs may add
focused workflows for task intake, backlog scheduling and preview, plan
confirmation, revalidation, Apply, and synchronization. Those capabilities
remain separate so each mutation can preserve its domain lifecycle and safety
boundary rather than being inferred in the frontend.
