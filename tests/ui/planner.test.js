import assert from "node:assert/strict";
import test from "node:test";

import { getPlanner, PlannerApiError } from "../../app/static/planner_api.js";
import {
  isEmptyPlanner,
  renderEmpty,
  renderError,
  renderLoading,
  renderPlanner,
} from "../../app/static/planner_view.js";

const emptyPlanner = () => ({
  generated_at: "2026-08-28T10:00:00Z",
  timezone: "Europe/Warsaw",
  today: [], upcoming: [], backlog: [], needs_attention: [],
  proposed_plans: [], confirmed_not_applied: [],
});

const session = (overrides = {}) => ({
  session_id: "session-1", task_id: "task-1", task_title: "Write proposal",
  start: "2026-08-28T08:15:00Z", end: "2026-08-28T09:45:00Z",
  timezone: "Europe/Warsaw", plan_id: "plan-1", plan_status: "confirmed",
  session_status: "confirmed", external_calendar: null, ...overrides,
});

const plan = (overrides = {}) => ({
  plan_id: "plan-1", task_id: "task-1", task_title: "Write proposal",
  backlog_entry_id: null, status: "proposed", readiness: "awaiting_confirmation",
  sessions: [session({ plan_status: "proposed", session_status: "proposed" })],
  total_scheduled_minutes: 90, mapped_session_count: 0, total_session_count: 1,
  revalidation_required: false, created_at: "2026-08-27T08:00:00Z",
  updated_at: "2026-08-27T08:00:00Z", confirmed_at: null, applied_at: null,
  calendar_context: {}, ...overrides,
});

test("loading, error, and overall empty states are explicit", () => {
  assert.match(renderLoading(), /Loading Planner/);
  assert.match(renderError("offline"), /could not be loaded/);
  assert.match(renderError("offline"), /offline/);
  const value = emptyPlanner();
  assert.equal(isEmptyPlanner(value), true);
  assert.match(renderEmpty(value), /Planner is clear/);
  assert.doesNotMatch(renderEmpty(value), /Today/);
});

test("partially populated Planner renders every section and section empty states", () => {
  const value = emptyPlanner();
  value.today = [session()];
  const html = renderPlanner(value);
  for (const heading of ["Today", "Upcoming", "Backlog", "Needs attention", "Proposed plans", "Confirmed / waiting for Apply"]) {
    assert.match(html, new RegExp(heading));
  }
  assert.match(html, /No sessions in the next 14 days/);
});

test("Today and Upcoming render task, current API times, duration, date, and state", () => {
  const value = emptyPlanner();
  value.today = [session()];
  value.upcoming = [session({ session_id: "session-2", task_title: "Future work", start: "2026-08-29T10:00:00Z", end: "2026-08-29T10:30:00Z" })];
  const html = renderPlanner(value);
  assert.match(html, /Write proposal/);
  assert.match(html, /Future work/);
  assert.match(html, /1h 30m/);
  assert.match(html, /30 min/);
  assert.match(html, /datetime="2026-08-28T08:15:00Z"/);
  assert.match(html, /datetime="2026-08-28T09:45:00Z"/);
  assert.match(html, /confirmed/);
});

test("Backlog renders remaining duration, reason, status, and defer information", () => {
  const value = emptyPlanner();
  value.backlog = [{ task_title: "Deferred task", remaining_duration_minutes: 45, reason: "insufficient_time", status: "deferred", deferred_until: "2026-09-01T09:00:00Z", next_review_at: null, note: "Wait" }];
  const html = renderPlanner(value);
  assert.match(html, /Deferred task/);
  assert.match(html, /45 min remaining/);
  assert.match(html, /insufficient time/);
  assert.match(html, /Deferred until/);
});

test("typed attention items are visibly distinguished without synthesizing failed plans", () => {
  const value = emptyPlanner();
  value.needs_attention = [
    { type: "consistency_finding", summary: "Sessions overlap", action: "review_consistency", severity: "warning" },
    { type: "external_calendar_change", summary: "Event moved", action: "process_external_change", severity: null },
    { type: "schedule_plan", summary: "Plan requires revalidation", action: "revalidate_plan", severity: null },
    { type: "schedule_plan", summary: "Plan partially applied", action: "retry_apply", severity: null },
  ];
  const html = renderPlanner(value);
  assert.match(html, /attention-consistency_finding/);
  assert.match(html, /attention-external_calendar_change/);
  assert.match(html, /requires revalidation/);
  assert.match(html, /partially applied/);
  assert.doesNotMatch(html, /failed/i);
});

test("proposed and confirmed-not-applied plans render sessions, totals, and backend states", () => {
  const value = emptyPlanner();
  value.proposed_plans = [plan()];
  value.confirmed_not_applied = [
    plan({ status: "revalidation_required", readiness: "needs_revalidation" }),
    plan({ plan_id: "plan-2", status: "applying", readiness: "apply_in_progress" }),
    plan({ plan_id: "plan-3", status: "partially_applied", readiness: "partially_applied" }),
  ];
  const html = renderPlanner(value);
  assert.match(html, /90 min/);
  assert.match(html, /awaiting confirmation/);
  assert.match(html, /revalidation required/);
  assert.match(html, /apply in progress/);
  assert.match(html, /partially applied/);
  assert.doesNotMatch(html, />Confirm</);
});

test("Planner client invokes only GET with user and horizon", async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => emptyPlanner() };
  };
  await getPlanner("user-1", { days: 14, fetchImpl });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, "GET");
  assert.match(calls[0].url, /^\/internal\/api\/planner\?/);
  assert.match(calls[0].url, /user_id=user-1/);
  assert.match(calls[0].url, /days=14/);
});

test("Planner client differentiates network, 404, 422, and malformed responses", async () => {
  await assert.rejects(
    getPlanner("user", { fetchImpl: async () => { throw new Error("offline"); } }),
    (error) => error instanceof PlannerApiError && error.kind === "network",
  );
  for (const status of [404, 422]) {
    await assert.rejects(
      getPlanner("user", { fetchImpl: async () => ({ ok: false, status, json: async () => ({ detail: "bad" }) }) }),
      (error) => error.status === status,
    );
  }
  await assert.rejects(
    getPlanner("user", { fetchImpl: async () => ({ ok: true, json: async () => ({}) }) }),
    (error) => error.kind === "malformed",
  );
});
