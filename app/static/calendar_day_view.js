import { formatSelectedDate, layoutTimedEvents, timelineTicks } from "./calendar_day_layout.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function time(value, timezone) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

function sourceLabel(item) {
  return item.source === "google_calendar" ? "Google Calendar" : "AI Calendar";
}

function continuation(item) {
  if (item.continuesBefore && item.continuesAfter) return "Continues from earlier and into the next day";
  if (item.continuesBefore) return "Continues from the previous day";
  if (item.continuesAfter) return "Continues into the next day";
  return "";
}

function withDayContinuation(item, dayStart, dayEnd) {
  return {
    ...item,
    continuesBefore: new Date(item.start).getTime() < new Date(dayStart).getTime(),
    continuesAfter: new Date(item.end).getTime() > new Date(dayEnd).getTime(),
  };
}

function eventCard(item, timezone) {
  const sourceClass = item.source === "google_calendar" ? "event-google" : "event-ai";
  const continued = continuation(item);
  return `<article class="timeline-event ${sourceClass}" style="--event-top:${item.topPercent}%;--event-height:${item.heightPercent}%;--event-column:${item.column};--event-columns:${item.columns}" aria-label="${escapeHtml(item.title || "Untitled event")}, ${time(item.start, timezone)} to ${time(item.end, timezone)}">
    <p class="event-time"><time datetime="${escapeHtml(item.start)}">${time(item.start, timezone)}</time>–<time datetime="${escapeHtml(item.end)}">${time(item.end, timezone)}</time></p>
    <h3>${escapeHtml(item.title || "Untitled event")}</h3>
    <p class="event-source"><span aria-hidden="true">${item.source === "google_calendar" ? "G" : "AI"}</span>${sourceLabel(item)}</p>
    ${continued ? `<p class="continuation">${continued}</p>` : ""}
  </article>`;
}

function allDayCard(item, dayStart, dayEnd) {
  const continued = continuation(withDayContinuation(item, dayStart, dayEnd));
  return `<article class="all-day-event ${item.source === "google_calendar" ? "event-google" : "event-ai"}">
    <span class="source-mark" aria-hidden="true">${item.source === "google_calendar" ? "G" : "AI"}</span>
    <div><h3>${escapeHtml(item.title || "Untitled event")}</h3><p>All day · ${sourceLabel(item)}</p>${continued ? `<p class="continuation">${continued}</p>` : ""}</div>
  </article>`;
}

function attentionCard(item) {
  return `<article class="attention-card"><span class="attention-icon" aria-hidden="true">!</span><div><h3>${escapeHtml(item.title || "Calendar item")}</h3><p>${escapeHtml(String(item.status).replaceAll("_", " "))}</p></div></article>`;
}


function warningCard() {
  return '<article class="attention-card warning-card"><span class="attention-icon" aria-hidden="true">!</span><div><h3>Calendar consistency needs review</h3><p>Some calendar data may not match the current AI Calendar session.</p></div></article>';
}


function unmatchedWarnings(warnings, attention) {
  const attentionSessionIds = new Set(attention
    .map((item) => item.scheduled_session?.session_id)
    .filter(Boolean)
    .map(String));
  return warnings.filter((warning) => (
    !warning.scheduled_session_id || !attentionSessionIds.has(String(warning.scheduled_session_id))
  ));
}

function syncMessage(status) {
  return {
    not_connected: ["Google Calendar is not connected", "Connect it from the existing development tools to see external events."],
    no_calendars_selected: ["No calendars selected for display", "Choose visible calendars in Calendar Settings when that interface becomes available."],
    partial: ["Some calendars could not be loaded", "Available calendar events are shown below."],
    error: ["Calendar events could not be loaded", "Your local AI Calendar sessions may still be shown."],
    reauthorization_required: ["Google Calendar needs to be reconnected", "Reauthorize it from the existing development tools."],
  }[status] ?? null;
}

export function renderLoading() {
  return '<span class="spinner" aria-hidden="true"></span> Loading calendar…';
}

export function renderRequestError() {
  return '<div class="request-error"><strong>Calendar could not be loaded.</strong><p>Please try again. No credentials or provider details are shown here.</p><button type="button" id="retry-calendar">Retry</button></div>';
}

export function renderCalendarDay(day) {
  const allDay = day.items.filter((item) => item.all_day && item.kind !== "needs_attention");
  const attention = day.items.filter((item) => item.kind === "needs_attention");
  const warnings = unmatchedWarnings(day.warnings, attention);
  const timed = layoutTimedEvents(day.items, day.day_start, day.day_end);
  const ticks = timelineTicks(day.day_start, day.day_end, day.timezone);
  const elapsedMinutes = (new Date(day.day_end) - new Date(day.day_start)) / 60000;
  const sync = syncMessage(day.sync_state.status);
  const syncClass = ["error", "reauthorization_required"].includes(day.sync_state.status) ? "sync-error" : "sync-warning";
  const empty = !allDay.length && !attention.length && !timed.length;
  return `<div class="calendar-meta"><span>${escapeHtml(day.timezone)}</span><span>${elapsedMinutes / 60} hour day</span></div>
    ${sync ? `<section class="sync-banner ${syncClass}" aria-label="Calendar sync status"><div><strong>${sync[0]}</strong><p>${sync[1]}</p></div>${["partial", "error", "reauthorization_required"].includes(day.sync_state.status) ? '<button type="button" id="retry-calendar">Retry</button>' : ""}</section>` : ""}
    ${attention.length || warnings.length ? `<section class="attention-section" aria-labelledby="attention-heading"><div class="section-title"><h2 id="attention-heading">Needs attention</h2><span>${attention.length + warnings.length}</span></div>${attention.map(attentionCard).join("")}${warnings.map(warningCard).join("")}</section>` : ""}
    ${allDay.length ? `<section class="all-day-section" aria-labelledby="all-day-heading"><div class="section-title"><h2 id="all-day-heading">All day</h2><span>${allDay.length}</span></div><div class="all-day-list">${allDay.map((item) => allDayCard(item, day.day_start, day.day_end)).join("")}</div></section>` : ""}
    ${empty ? '<div class="empty-day"><h2>No displayed events for this day</h2><p>This does not necessarily mean the time is available for automatic scheduling.</p></div>' : ""}
    ${timed.length ? `<section class="timeline-section" aria-label="Day timeline"><div class="timeline" style="--timeline-minutes:${elapsedMinutes}">
      <div class="time-axis" aria-hidden="true">${ticks.map((tick) => `<div class="time-tick" style="--tick-minute:${tick.elapsedMinutes}"><span>${escapeHtml(tick.label)}</span></div>`).join("")}</div>
      <div class="event-lane">${timed.map((item) => eventCard(item, day.timezone)).join("")}</div>
    </div></section>` : ""}`;
}

export function selectedDateHeading(date, timezone) {
  return formatSelectedDate(date, timezone);
}
