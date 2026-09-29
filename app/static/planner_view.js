const SECTION_KEYS = [
  "today", "upcoming", "backlog", "needs_attention",
  "proposed_plans", "confirmed_not_applied",
];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function label(value) {
  return escapeHtml(String(value ?? "unknown").replaceAll("_", " "));
}

function dateTime(value, timezone, includeDate = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    ...(includeDate ? { weekday: "short", month: "short", day: "numeric" } : {}),
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function dateOnly(value, timezone) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone, weekday: "long", month: "short", day: "numeric",
  }).format(date);
}

function duration(start, end) {
  const minutes = Math.round((new Date(end) - new Date(start)) / 60000);
  if (!Number.isFinite(minutes)) return "—";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
}

function badge(value, extra = "") {
  return `<span class="badge ${extra}">${label(value)}</span>`;
}

function sessionCard(session, timezone, upcoming = false) {
  const when = upcoming
    ? `<p class="date">${dateOnly(session.start, timezone)}</p>`
    : "";
  return `<article class="card session-card">
    ${when}<h3>${escapeHtml(session.task_title)}</h3>
    <p class="time"><time datetime="${escapeHtml(session.start)}">${dateTime(session.start, timezone)}</time>–<time datetime="${escapeHtml(session.end)}">${dateTime(session.end, timezone)}</time> <span>${duration(session.start, session.end)}</span></p>
    <div class="badges">${badge(session.plan_status)}${badge(session.session_status)}</div>
  </article>`;
}

function backlogCard(item, timezone) {
  const review = item.deferred_until
    ? `Deferred until ${dateTime(item.deferred_until, timezone, true)}`
    : item.next_review_at
      ? `Review ${dateTime(item.next_review_at, timezone, true)}`
      : "No review date";
  return `<article class="card"><h3>${escapeHtml(item.task_title)}</h3>
    <p><strong>${escapeHtml(item.remaining_duration_minutes)} min remaining</strong></p>
    <div class="badges">${badge(item.reason)}${badge(item.status)}</div>
    <p class="meta">${review}${item.note ? ` · ${escapeHtml(item.note)}` : ""}</p></article>`;
}

function attentionCard(item) {
  return `<article class="card attention attention-${escapeHtml(item.type)}">
    <div class="badges">${badge(item.type, "attention-badge")}${item.severity ? badge(item.severity) : ""}</div>
    <h3>${escapeHtml(item.summary)}</h3><p class="meta">Expected next step: ${label(item.action)}</p>
  </article>`;
}

function planCard(plan, timezone) {
  const sessions = plan.sessions.map((session) =>
    `<li><time datetime="${escapeHtml(session.start)}">${dateTime(session.start, timezone, true)}</time>–<time datetime="${escapeHtml(session.end)}">${dateTime(session.end, timezone)}</time> <span>${duration(session.start, session.end)}</span></li>`
  ).join("");
  return `<article class="card plan-card"><div><h3>${escapeHtml(plan.task_title)}</h3>
    <div class="badges">${badge(plan.status)}${badge(plan.readiness)}</div></div>
    <p><strong>${escapeHtml(plan.total_scheduled_minutes)} min</strong> across ${escapeHtml(plan.total_session_count)} session${plan.total_session_count === 1 ? "" : "s"}</p>
    <ol class="session-list">${sessions}</ol></article>`;
}

function section(title, items, renderer, emptyMessage, className = "") {
  const body = items.length
    ? `<div class="card-grid ${className}">${items.map(renderer).join("")}</div>`
    : `<p class="section-empty">${escapeHtml(emptyMessage)}</p>`;
  return `<section><div class="section-heading"><h2>${title}</h2><span>${items.length}</span></div>${body}</section>`;
}

export function isEmptyPlanner(planner) {
  return SECTION_KEYS.every((key) => planner[key].length === 0);
}

export function renderLoading() {
  return '<span class="spinner" aria-hidden="true"></span> Loading Planner…';
}

export function renderError(message) {
  return `<div class="error"><strong>Planner could not be loaded.</strong><p>${escapeHtml(message)}</p><button type="button" id="retry-planner">Try again</button></div>`;
}

export function renderEmpty(planner) {
  return `<div class="overall-empty"><h2>Your Planner is clear</h2><p>There are no scheduled sessions, backlog items, proposed plans, or items needing attention.</p><p class="meta">Timezone: ${escapeHtml(planner.timezone)}</p></div>`;
}

export function renderPlanner(planner) {
  const timezone = planner.timezone;
  return `<div class="planner-meta"><span>Timezone: <strong>${escapeHtml(timezone)}</strong></span><span>Updated ${dateTime(planner.generated_at, timezone, true)}</span></div>
    ${section("Today", planner.today, (item) => sessionCard(item, timezone), "Nothing scheduled today.")}
    ${section("Upcoming", planner.upcoming, (item) => sessionCard(item, timezone, true), "No sessions in the next 14 days.")}
    ${section("Backlog", planner.backlog, (item) => backlogCard(item, timezone), "No active or deferred backlog items.")}
    ${section("Needs attention", planner.needs_attention, attentionCard, "Nothing needs attention.", "attention-grid")}
    ${section("Proposed plans", planner.proposed_plans, (item) => planCard(item, timezone), "No plans are awaiting confirmation.")}
    ${section("Confirmed / waiting for Apply", planner.confirmed_not_applied, (item) => planCard(item, timezone), "No confirmed plans are waiting for Apply.")}`;
}
