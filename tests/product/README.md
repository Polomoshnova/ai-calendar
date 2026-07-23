# Scheduling Product Validation

This directory contains realistic scheduling scenarios for human product review.
The suite helps product and engineering teams judge whether a generated schedule
feels useful, understandable, and trustworthy. It is deliberately separate from
the regression tests in `tests/scenarios`: product observations are not automated
pass/fail assertions.

The runner calls the same pure availability and scheduling core used by
`POST /api/v1/scheduling/preview`. It does not require PostgreSQL and does not
create application records.

## Run the suite

From the repository root:

```bash
python -m tests.product.runner
```

Every `examples/*.json` file is discovered in filename order. A malformed
scenario is reported, but the runner continues with the remaining files. The
final summary reports execution failures only; it does not claim that scheduling
quality passed or failed.

## Scenario format

Each JSON document contains:

- `name`: short human-readable scenario name;
- `description`: context and user intent;
- `planning_window`: timezone-aware ISO-8601 `start` and `end`;
- `user_preferences`: timezone, seven-day working-hours map, and scheduler
  preferences;
- `busy_intervals`: explicit timezone-aware intervals;
- `tasks`: plain scheduler inputs plus a display `title`;
- `expected_observations`: review prompts written as documentation, never exact
  timestamp assertions.

Working hours must include all seven lowercase weekday names. Empty weekday lists
mean unavailable days. Times are local wall-clock `HH:MM` values interpreted in
the scenario timezone.

## Add a scenario

1. Copy the closest file in `examples/`.
2. Give it a numbered, descriptive filename such as
   `11_late_deadline_tradeoff.json`.
3. Use stable, descriptive task IDs.
4. Keep the scenario focused on one product question where possible.
5. Describe desired qualities in `expected_observations`; do not encode exact
   output timestamps.
6. Run `python -m tests.product.runner` and inspect the complete output.
7. Run Ruff, formatting, and mypy before submitting the change.

## Review process

Scenario changes should be reviewed by at least one product-minded reviewer and
one engineer familiar with scheduler constraints. Reviewers should discuss:

- whether hard constraints were respected;
- whether task ordering and fragmentation feel reasonable;
- whether unscheduled reasons are honest and actionable;
- whether reason codes match the visible result;
- whether the schedule is stable and easy to explain;
- whether the expected observations still express the intended product behavior.

If a result looks wrong, first record the observation and reproduce it in a
focused scenario. Algorithm changes belong in a separate change with normal
regression tests; do not turn subjective product observations into brittle exact
timestamp assertions.
