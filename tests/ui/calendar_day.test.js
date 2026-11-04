import assert from "node:assert/strict";
import test from "node:test";

import { getCalendarDay, CalendarDayApiError } from "../../app/static/calendar_day_api.js";
import {
  formatSelectedDate,
  layoutTimedEvents,
  shiftCalendarDate,
  timelineTicks,
  todayInTimezone,
} from "../../app/static/calendar_day_layout.js";
import {
  renderCalendarDay,
  renderLoading,
  renderRequestError,
} from "../../app/static/calendar_day_view.js";

const START = "2026-11-02T23:00:00Z";
const END = "2026-11-03T23:00:00Z";

const item = (id, start, end, overrides = {}) => ({
  id,
  source: "google_calendar",
  kind: "event",
  title: `Event ${id}`,
  start,
  end,
  all_day: false,
  status: "confirmed",
  scheduled_session: null,
  ...overrides,
});

const day = (overrides = {}) => ({
  date: "2026-11-03",
  timezone: "Europe/Warsaw",
  day_start: START,
  day_end: END,
  generated_at: "2026-11-03T10:00:00Z",
  sync_state: { status: "fresh", partial: false, errors: [] },
  warnings: [],
  items: [],
  ...overrides,
});

test("60, 120, and 30 minute events have proportional heights", () => {
  const events = layoutTimedEvents([
    item("60", "2026-11-03T08:00:00Z", "2026-11-03T09:00:00Z"),
    item("120", "2026-11-03T10:00:00Z", "2026-11-03T12:00:00Z"),
    item("30", "2026-11-03T13:00:00Z", "2026-11-03T13:30:00Z"),
  ], START, END);
  const heights = Object.fromEntries(events.map((event) => [event.id, event.heightPercent]));
  assert.equal(heights["120"], heights["60"] * 2);
  assert.equal(heights["30"], heights["60"] / 2);
});

test("two and three overlapping events receive distinct predictable columns", () => {
  const two = layoutTimedEvents([
    item("a", "2026-11-03T08:00:00Z", "2026-11-03T10:00:00Z"),
    item("b", "2026-11-03T09:00:00Z", "2026-11-03T11:00:00Z"),
  ], START, END);
  assert.deepEqual(two.map(({ column, columns }) => [column, columns]), [[0, 2], [1, 2]]);
  const three = layoutTimedEvents([
    item("a", "2026-11-03T08:00:00Z", "2026-11-03T11:00:00Z"),
    item("b", "2026-11-03T08:30:00Z", "2026-11-03T10:00:00Z"),
    item("c", "2026-11-03T09:00:00Z", "2026-11-03T09:30:00Z"),
  ], START, END);
  assert.deepEqual(three.map(({ column, columns }) => [column, columns]), [[0, 3], [1, 3], [2, 3]]);
});

test("events sharing only a half-open boundary do not overlap", () => {
  const events = layoutTimedEvents([
    item("a", "2026-11-03T08:00:00Z", "2026-11-03T09:00:00Z"),
    item("b", "2026-11-03T09:00:00Z", "2026-11-03T10:00:00Z"),
  ], START, END);
  assert.deepEqual(events.map(({ column, columns }) => [column, columns]), [[0, 1], [0, 1]]);
});

test("cross-midnight events are clipped without changing real times", () => {
  const [event] = layoutTimedEvents([
    item("cross", "2026-11-02T22:30:00Z", "2026-11-03T00:30:00Z"),
  ], START, END);
  assert.equal(event.start, "2026-11-02T22:30:00Z");
  assert.equal(event.continuesBefore, true);
  assert.equal(event.continuesAfter, false);
  assert.equal(event.elapsedMinutes, 90);
  assert.equal(event.topPercent, 0);
});

test("spring-forward uses a 23-hour elapsed timeline", () => {
  const start = "2026-03-28T23:00:00Z";
  const end = "2026-03-29T22:00:00Z";
  const ticks = timelineTicks(start, end, "Europe/Warsaw");
  assert.equal((new Date(end) - new Date(start)) / 3_600_000, 23);
  assert.equal(ticks.length, 24);
  assert.equal(ticks.some((tick) => tick.label.startsWith("02:00")), false);
});

test("autumn fallback preserves both 02:30 instances as distinct positions", () => {
  const start = "2026-10-24T22:00:00Z";
  const end = "2026-10-25T23:00:00Z";
  const events = layoutTimedEvents([
    item("first", "2026-10-25T00:30:00Z", "2026-10-25T00:45:00Z"),
    item("second", "2026-10-25T01:30:00Z", "2026-10-25T01:45:00Z"),
  ], start, end);
  assert.equal((new Date(end) - new Date(start)) / 3_600_000, 25);
  assert.notEqual(events[0].topPercent, events[1].topPercent);
  const repeated = timelineTicks(start, end, "Europe/Warsaw").filter((tick) => tick.label.startsWith("02:00"));
  assert.equal(repeated.length, 2);
  assert.notEqual(repeated[0].label, repeated[1].label);
});

test("calendar date navigation crosses month and year boundaries", () => {
  assert.equal(shiftCalendarDate("2026-11-03", -1), "2026-11-02");
  assert.equal(shiftCalendarDate("2026-11-03", 1), "2026-11-04");
  assert.equal(shiftCalendarDate("2026-03-01", -1), "2026-02-28");
  assert.equal(shiftCalendarDate("2026-12-31", 1), "2027-01-01");
  assert.equal(todayInTimezone("Europe/Warsaw", new Date("2026-11-03T23:30:00Z")), "2026-11-04");
  assert.match(formatSelectedDate("2026-11-03", "Europe/Warsaw"), /2026/);
});

test("loading, request error, and empty states are explicit", () => {
  assert.match(renderLoading(), /Loading calendar/);
  assert.match(renderRequestError(), /Retry/);
  const html = renderCalendarDay(day());
  assert.match(html, /No displayed events/);
  assert.match(html, /does not necessarily mean/);
});

test("sync states render without treating provider errors as empty success", () => {
  for (const [status, message] of [
    ["not_connected", "not connected"],
    ["no_calendars_selected", "No calendars selected"],
    ["partial", "Some calendars could not be loaded"],
    ["error", "could not be loaded"],
    ["reauthorization_required", "needs to be reconnected"],
  ]) {
    const html = renderCalendarDay(day({ sync_state: { status, partial: status === "partial", errors: status === "fresh" ? [] : [{ code: "provider" }] } }));
    assert.match(html, new RegExp(message, "i"));
  }
});

test("needs-attention and warnings render once without exposing provider details", () => {
  const attention = item("attention", "2026-11-03T10:00:00Z", "2026-11-03T11:00:00Z", {
    kind: "needs_attention",
    status: "unverified",
    scheduled_session: { session_id: "session-1" },
  });
  const html = renderCalendarDay(day({ items: [attention], warnings: [{ code: "mapped_event_not_returned" }] }));
  assert.equal((html.match(/Needs attention/g) ?? []).length, 1);
  assert.match(html, /unverified/);
  assert.match(html, /Calendar consistency needs review/);
  assert.doesNotMatch(html, /timeline-event/);
  const duplicate = renderCalendarDay(day({
    items: [attention],
    warnings: [{ code: "mapped_event_not_returned", message: "internal provider detail", scheduled_session_id: "session-1" }],
  }));
  assert.equal((duplicate.match(/attention-card/g) ?? []).length, 1);
  assert.doesNotMatch(duplicate, /internal provider detail/);
});

test("Google, AI, all-day, multi-day, untitled, and long titles render safely", () => {
  const html = renderCalendarDay(day({ items: [
    item("google", "2026-11-03T08:00:00Z", "2026-11-03T09:00:00Z"),
    item("ai", "2026-11-03T10:00:00Z", "2026-11-03T11:00:00Z", { source: "ai_calendar", kind: "scheduled_session", title: "AI work" }),
    item("all", START, END, { all_day: true, title: "Holiday" }),
    item("multi", "2026-11-01T23:00:00Z", "2026-11-05T23:00:00Z", { all_day: true, title: "Conference" }),
    item("none", "2026-11-03T12:00:00Z", "2026-11-03T13:00:00Z", { title: "" }),
    item("long", "2026-11-03T14:00:00Z", "2026-11-03T15:00:00Z", { title: "A very long calendar title that must wrap without overflowing the mobile viewport <script>" }),
  ] }));
  assert.match(html, /Google Calendar/);
  assert.match(html, /AI Calendar/);
  assert.equal((html.match(/All day ·/g) ?? []).length, 2);
  assert.match(html, /Continues from earlier and into the next day/);
  assert.match(html, /Untitled event/);
  assert.doesNotMatch(html, /<script>/);
});

test("Calendar API performs one GET without user identity and validates response", async () => {
  const calls = [];
  const value = day();
  const result = await getCalendarDay("2026-11-03", { fetchImpl: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => value };
  } });
  assert.equal(result, value);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].url, "/api/v1/calendar/day?date=2026-11-03");
  assert.doesNotMatch(calls[0].url, /user_id|X-User-Id/i);
});

test("Calendar API distinguishes abort, network, status, and malformed responses", async () => {
  const aborted = new Error("aborted");
  aborted.name = "AbortError";
  await assert.rejects(getCalendarDay("2026-11-03", { fetchImpl: async () => { throw aborted; } }), (error) => error.name === "AbortError");
  await assert.rejects(getCalendarDay("2026-11-03", { fetchImpl: async () => { throw new Error("offline"); } }), (error) => error instanceof CalendarDayApiError && error.kind === "network");
  await assert.rejects(getCalendarDay("2026-11-03", { fetchImpl: async () => ({ ok: false, status: 500 }) }), (error) => error.status === 500);
  await assert.rejects(getCalendarDay("2026-11-03", { fetchImpl: async () => ({ ok: true, json: async () => ({}) }) }), (error) => error.kind === "malformed");
});
