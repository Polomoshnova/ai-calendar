const weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const problems = [
  "wrong priority", "deadline handling", "excessive splitting",
  "insufficient splitting", "poor time-of-day choice", "unnecessary fragmentation",
  "break issue", "task left unscheduled unexpectedly", "unclear explanation", "other",
];
const state = {
  mode: "existing_user",
  timezone: "UTC",
  users: [],
  tasks: [],
  busy: [],
  scenario: null,
  preview: null,
  normalizedInputs: null,
};
const $ = (id) => document.getElementById(id);

function showMessage(text, kind = "error") {
  const box = $("message");
  box.textContent = text;
  box.className = `message ${kind}`;
  box.hidden = false;
}
function clearMessage() { $("message").hidden = true; }
function apiError(body) {
  if (!body) return "Request failed";
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((item) => item.msg).join("\n");
  return JSON.stringify(body);
}
async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  if (!response.ok) {
    let body;
    try { body = await response.json(); } catch { body = null; }
    throw new Error(apiError(body));
  }
  return response.status === 204 ? null : response.json();
}
function element(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function buildWorkingHours() {
  const container = $("working-hours");
  weekdays.forEach((day) => {
    const block = element("div", undefined, "working-day");
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.id = `hours-${day}-enabled`;
    const label = element("label");
    label.append(enabled, document.createTextNode(` ${day.slice(0, 3)}`));
    const start = document.createElement("input");
    start.type = "time"; start.id = `hours-${day}-start`; start.value = "09:00";
    const end = document.createElement("input");
    end.type = "time"; end.id = `hours-${day}-end`; end.value = "18:00";
    enabled.addEventListener("change", () => {
      start.disabled = end.disabled = !enabled.checked;
    });
    block.append(label, start, end);
    container.append(block);
  });
}
function setPreferences(preferences, stored) {
  state.timezone = preferences.timezone;
  $("timezone").textContent = state.timezone;
  $("stored-preferences").textContent = state.mode === "product_scenario"
    ? "Temporary scenario values"
    : stored ? "Yes" : "No — defaults shown";
  weekdays.forEach((day) => {
    const windows = preferences.working_hours[day] || [];
    const enabled = $(`hours-${day}-enabled`);
    enabled.checked = windows.length > 0;
    $(`hours-${day}-start`).value = windows[0]?.start || "09:00";
    $(`hours-${day}-end`).value = windows[0]?.end || "18:00";
    $(`hours-${day}-start`).disabled = $(`hours-${day}-end`).disabled = !enabled.checked;
  });
  $("preferred-task-time").value = preferences.preferred_task_time || "any";
  $("minimum-break").value = preferences.minimum_break_minutes ?? 0;
  $("deep-work-cutoff").value = preferences.no_deep_work_after || "";
  $("default-session").value = preferences.default_minimum_session_minutes ?? 15;
}
function preferencesFromForm() {
  const working_hours = {};
  weekdays.forEach((day) => {
    working_hours[day] = $(`hours-${day}-enabled`).checked
      ? [{start: $(`hours-${day}-start`).value, end: $(`hours-${day}-end`).value}]
      : [];
  });
  return {
    timezone: state.timezone,
    working_hours,
    preferred_task_time: $("preferred-task-time").value,
    minimum_break_minutes: Number($("minimum-break").value),
    no_deep_work_after: $("deep-work-cutoff").value || null,
    default_minimum_session_minutes: Number($("default-session").value),
  };
}

function offsetAt(instantMs, timezone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(instantMs));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const represented = Date.UTC(
    Number(values.year), Number(values.month) - 1, Number(values.day),
    Number(values.hour), Number(values.minute), Number(values.second),
  );
  return represented - instantMs;
}
function localInputToIso(value, timezone = state.timezone) {
  if (!value) return null;
  const [date, clock] = value.split("T");
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute] = clock.split(":").map(Number);
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  let instant = guess - offsetAt(guess, timezone);
  instant = guess - offsetAt(instant, timezone);
  return new Date(instant).toISOString();
}
function isoToLocalInput(value, timezone = state.timezone) {
  if (!value) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(value));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
}
function localDisplay(value, options = {}) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: state.timezone, dateStyle: options.date ? "medium" : undefined,
    timeStyle: "short",
  }).format(new Date(value));
}

async function loadUsers() {
  state.users = await request("/internal/api/users");
  const select = $("user-select");
  select.replaceChildren();
  state.users.forEach((user) => {
    const option = element("option", `${user.email} (${user.timezone})`);
    option.value = user.id;
    select.append(option);
  });
  if (state.users.length) await loadExistingUser();
  else showMessage("No users exist. Create a development user before using existing-user mode.");
}
async function loadExistingUser() {
  const userId = $("user-select").value;
  if (!userId) return;
  const [envelope, tasks] = await Promise.all([
    request(`/internal/api/users/${userId}/preferences`),
    request(`/api/v1/tasks?user_id=${encodeURIComponent(userId)}`),
  ]);
  state.tasks = tasks;
  setPreferences(envelope.preferences, envelope.has_stored_preferences);
  renderTasks();
  setDefaultPlanningWindow();
}
async function loadScenarios() {
  const scenarios = await request("/internal/api/scenarios");
  const select = $("scenario-select");
  scenarios.forEach((scenario) => {
    const option = element("option", scenario.name);
    option.value = scenario.filename;
    option.title = scenario.description;
    select.append(option);
  });
}
async function loadScenario() {
  const filename = $("scenario-select").value;
  if (!filename) return;
  state.scenario = await request(`/internal/api/scenarios/${encodeURIComponent(filename)}`);
  state.tasks = state.scenario.tasks.map((task) => ({...task, status: "pending"}));
  state.busy = state.scenario.busy_intervals.map((item) => ({...item, label: ""}));
  setPreferences(state.scenario.user_preferences, false);
  $("planning-start").value = isoToLocalInput(state.scenario.planning_window.start);
  $("planning-end").value = isoToLocalInput(state.scenario.planning_window.end);
  const observations = $("expected-observations");
  observations.replaceChildren(element("strong", "Expected observations"));
  const list = document.createElement("ul");
  state.scenario.expected_observations.forEach((item) => list.append(element("li", item)));
  observations.append(list);
  observations.hidden = false;
  renderTasks();
  renderBusy();
  resetPreview();
}
function setDefaultPlanningWindow() {
  const now = new Date();
  const start = new Date(now);
  start.setMinutes(0, 0, 0);
  const end = new Date(start);
  let workingDays = 0;
  while (workingDays < 5) {
    end.setDate(end.getDate() + 1);
    if (![0, 6].includes(end.getDay())) workingDays += 1;
  }
  $("planning-start").value = isoToLocalInput(start.toISOString());
  $("planning-end").value = isoToLocalInput(end.toISOString());
}

function taskFromForm() {
  const id = $("task-id").value || (state.mode === "product_scenario"
    ? `task-${crypto.randomUUID()}` : null);
  return {
    id,
    title: $("task-title").value.trim(),
    duration_minutes: Number($("task-duration").value),
    priority: $("task-priority").value,
    status: $("task-status").value,
    earliest_start: localInputToIso($("task-earliest").value),
    deadline: localInputToIso($("task-deadline").value),
    preferred_time_of_day: $("task-preferred").value,
    is_splittable: $("task-splittable").checked,
    minimum_session_minutes: Number($("task-minimum-session").value),
    maximum_sessions_per_day: Number($("task-maximum-sessions").value),
  };
}
function clearTaskForm() {
  $("task-form").reset();
  $("task-id").value = "";
  $("task-priority").value = "medium";
  $("task-status").value = "pending";
  $("task-preferred").value = "any";
  $("task-minimum-session").value = 15;
  $("task-maximum-sessions").value = 1;
}
function editTask(task) {
  $("task-id").value = task.id;
  $("task-title").value = task.title;
  $("task-duration").value = task.duration_minutes;
  $("task-priority").value = task.priority;
  $("task-status").value = task.status || "pending";
  $("task-earliest").value = isoToLocalInput(task.earliest_start);
  $("task-deadline").value = isoToLocalInput(task.deadline);
  $("task-preferred").value = task.preferred_time_of_day || "any";
  $("task-splittable").checked = task.is_splittable;
  $("task-minimum-session").value = task.minimum_session_minutes || 15;
  $("task-maximum-sessions").value = task.maximum_sessions_per_day || 1;
  $("task-title").focus();
}
async function saveTask(event) {
  event.preventDefault();
  clearMessage();
  const task = taskFromForm();
  try {
    if (state.mode === "product_scenario") {
      const index = state.tasks.findIndex((item) => item.id === task.id);
      if (index >= 0) state.tasks[index] = task;
      else state.tasks.push(task);
    } else {
      const user_id = $("user-select").value;
      const payload = {...task, user_id};
      delete payload.id;
      delete payload.status;
      if ($("task-id").value) {
        delete payload.user_id;
        await request(`/api/v1/tasks/${$("task-id").value}`, {
          method: "PATCH", body: JSON.stringify({...payload, status: task.status}),
        });
      } else {
        await request("/api/v1/tasks", {method: "POST", body: JSON.stringify(payload)});
      }
      await loadExistingUser();
    }
    clearTaskForm();
    renderTasks();
    resetPreview();
  } catch (error) { showMessage(error.message); }
}
async function taskAction(task, action) {
  try {
    if (state.mode === "product_scenario") {
      if (action === "delete") state.tasks = state.tasks.filter((item) => item.id !== task.id);
      else task.status = action;
    } else if (action === "delete") {
      await request(`/api/v1/tasks/${task.id}`, {method: "DELETE"});
      await loadExistingUser();
    } else {
      await request(`/api/v1/tasks/${task.id}`, {
        method: "PATCH", body: JSON.stringify({status: action}),
      });
      await loadExistingUser();
    }
    renderTasks();
    resetPreview();
  } catch (error) { showMessage(error.message); }
}
function renderTasks() {
  const body = $("task-list");
  body.replaceChildren();
  state.tasks.forEach((task) => {
    const row = document.createElement("tr");
    row.append(
      element("td", task.title),
      element("td", `${task.duration_minutes} min`),
      element("td", task.priority),
      element("td", task.status || "pending"),
      element("td", `${task.earliest_start ? localDisplay(task.earliest_start, {date: true}) : "—"} → ${task.deadline ? localDisplay(task.deadline, {date: true}) : "—"}`),
    );
    const actions = document.createElement("td");
    [["Edit", () => editTask(task)], ["Cancel", () => taskAction(task, "cancelled")],
      ["Complete", () => taskAction(task, "completed")], ["Delete", () => taskAction(task, "delete")]]
      .forEach(([label, handler]) => {
        const button = element("button", label);
        if (label === "Delete") button.className = "danger";
        button.type = "button"; button.addEventListener("click", handler);
        actions.append(button);
      });
    row.append(actions);
    body.append(row);
  });
}

function renderBusy() {
  const list = $("busy-list");
  list.replaceChildren();
  state.busy.forEach((busy, index) => {
    const item = element("li", `${busy.label || "Busy"}: ${localDisplay(busy.start, {date: true})} – ${localDisplay(busy.end, {date: true})}`);
    const remove = element("button", "Remove", "danger");
    remove.type = "button";
    remove.addEventListener("click", () => { state.busy.splice(index, 1); renderBusy(); resetPreview(); });
    item.append(" ", remove); list.append(item);
  });
}
function addBusy(event) {
  event.preventDefault();
  state.busy.push({
    start: localInputToIso($("busy-start").value),
    end: localInputToIso($("busy-end").value),
    label: $("busy-label").value.trim(),
  });
  event.target.reset(); renderBusy(); resetPreview();
}
function intervalPayload(item) { return {start: item.start, end: item.end}; }
function temporaryTaskPayload(task) {
  const payload = {
    id: task.id, title: task.title, duration_minutes: task.duration_minutes,
    priority: task.priority, earliest_start: task.earliest_start || null,
    deadline: task.deadline || null, preferred_time_of_day: task.preferred_time_of_day || "any",
    is_splittable: task.is_splittable || false,
    minimum_session_minutes: task.minimum_session_minutes || 15,
    maximum_sessions_per_day: task.maximum_sessions_per_day || 1,
  };
  return payload;
}
function tasksUsedForPlanning(tasks, planningWindow) {
  const start = new Date(planningWindow.start);
  const end = new Date(planningWindow.end);
  return tasks.filter((task) =>
    (task.status || "pending") === "pending"
    && (!task.earliest_start || new Date(task.earliest_start) < end)
    && (!task.deadline || new Date(task.deadline) > start)
  );
}
async function generatePreview() {
  clearMessage();
  $("generate").disabled = true; $("loading").hidden = false;
  try {
    const planning_window = {
      start: localInputToIso($("planning-start").value),
      end: localInputToIso($("planning-end").value),
    };
    const busy_intervals = state.busy.map(intervalPayload);
    let payload;
    let preferences;
    let tasks;
    let tasksUsed;
    if (state.mode === "existing_user") {
      const userId = $("user-select").value;
      const [envelope, currentTasks] = await Promise.all([
        request(`/internal/api/users/${userId}/preferences`),
        request(`/api/v1/tasks?user_id=${encodeURIComponent(userId)}`),
      ]);
      preferences = envelope.preferences;
      tasks = currentTasks;
      tasksUsed = tasksUsedForPlanning(tasks, planning_window);
      state.tasks = currentTasks;
      payload = {mode: state.mode, user_id: userId, planning_window, busy_intervals};
    } else {
      preferences = preferencesFromForm();
      tasks = state.tasks.map((task) => ({...task}));
      tasksUsed = tasksUsedForPlanning(tasks, planning_window);
      payload = {
        mode: state.mode, timezone: state.timezone, planning_window, busy_intervals,
        preferences, tasks: tasksUsed.map(temporaryTaskPayload),
      };
    }
    state.preview = await request("/internal/api/scheduling/preview", {
      method: "POST", body: JSON.stringify(payload),
    });
    state.normalizedInputs = {
      user_timezone: state.timezone,
      planning_window,
      preferences_used: preferences,
      tasks: tasksUsed.map(temporaryTaskPayload),
      busy_intervals: state.busy.map((item) => ({...intervalPayload(item), label: item.label || null})),
    };
    renderPreview();
  } catch (error) {
    showMessage(`Preview validation failed:\n${error.message}`);
  } finally {
    $("generate").disabled = false; $("loading").hidden = true;
  }
}
function resetPreview() {
  state.preview = null; state.normalizedInputs = null;
  $("preview").hidden = true; $("preview-empty").hidden = false;
}
function renderPreview() {
  $("preview-empty").hidden = true; $("preview").hidden = false;
  const titles = new Map(state.normalizedInputs.tasks.map((task) => [String(task.id), task.title]));
  const timeline = $("timeline"); timeline.replaceChildren();
  const items = [
    ...state.preview.scheduled_blocks.map((block) => ({...block, kind: "task"})),
    ...state.busy.map((busy) => ({...busy, kind: "busy"})),
  ].sort((a, b) => new Date(a.start) - new Date(b.start));
  const taskTotals = {};
  state.preview.scheduled_blocks.forEach((block) => { taskTotals[block.task_id] = (taskTotals[block.task_id] || 0) + 1; });
  const taskSessions = {};
  const days = new Map();
  items.forEach((item) => {
    const day = new Intl.DateTimeFormat("en-CA", {timeZone: state.timezone, year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date(item.start));
    if (!days.has(day)) days.set(day, []);
    days.get(day).push(item);
  });
  days.forEach((dayItems, day) => {
    const group = element("div", undefined, "day");
    group.append(element("h4", day));
    dayItems.forEach((item) => {
      const row = element("div", undefined, `timeline-item ${item.kind === "busy" ? "busy" : ""}`);
      row.append(element("strong", `${localDisplay(item.start)}–${localDisplay(item.end)}`));
      const details = document.createElement("div");
      if (item.kind === "busy") {
        details.append(element("strong", item.label || "Busy interval"));
      } else {
        taskSessions[item.task_id] = (taskSessions[item.task_id] || 0) + 1;
        const duration = Math.round((new Date(item.end) - new Date(item.start)) / 60000);
        const session = taskTotals[item.task_id] > 1 ? ` · session ${taskSessions[item.task_id]}` : "";
        details.append(element("strong", `${titles.get(item.task_id) || item.task_id} · ${duration} min${session}`));
        details.append(element("div", `Reasons: ${item.reason_codes.join(", ")}`, "codes"));
        details.append(element("div", `Scores: ${item.score_components.map((score) => `${score.name} ${score.value}`).join(", ")}`, "codes"));
      }
      row.append(details); group.append(row);
    });
    timeline.append(group);
  });
  const summary = $("result-summary"); summary.replaceChildren();
  [["Scheduler version", state.preview.scheduler_version], ["User timezone", state.timezone],
    ["Scheduled blocks", state.preview.scheduled_blocks.length], ["Unscheduled tasks", state.preview.unscheduled_tasks.length]]
    .forEach(([key, value]) => summary.append(element("dt", key), element("dd", String(value))));
  renderIntervalList("free-intervals", state.preview.free_intervals);
  renderSimpleList("unscheduled", state.preview.unscheduled_tasks.map((item) =>
    `${titles.get(item.task_id) || item.task_id}: ${item.remaining_minutes} min (${item.reason_code})`));
  renderSimpleList("warnings", state.preview.warnings.map((item) => `${item.code}: ${item.task_ids.join(", ")}`));
  $("raw-json").textContent = JSON.stringify(state.preview, null, 2);
}
function renderIntervalList(id, intervals) {
  renderSimpleList(id, intervals.map((item) => `${localDisplay(item.start, {date: true})} – ${localDisplay(item.end, {date: true})}`));
}
function renderSimpleList(id, values) {
  const list = $(id); list.replaceChildren();
  if (!values.length) list.append(element("li", "None"));
  values.forEach((value) => list.append(element("li", value)));
}
async function exportReview() {
  if (!state.preview || !state.normalizedInputs) return;
  try {
    const observed_problems = [...document.querySelectorAll("#problem-options input:checked")].map((item) => item.value);
    const payload = await request("/internal/api/review-export", {
      method: "POST",
      body: JSON.stringify({
        normalized_inputs: state.normalizedInputs,
        generated_preview_result: state.preview,
        review: {
          score: Number($("review-score").value),
          verdict: $("review-verdict").value,
          notes: $("review-notes").value,
          observed_problems,
        },
      }),
    });
    const blob = new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"});
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `scheduling-review-${new Date().toISOString().replaceAll(":", "-")}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) { showMessage(error.message); }
}
async function savePreferences() {
  try {
    await request(`/internal/api/users/${$("user-select").value}/preferences`, {
      method: "PUT", body: JSON.stringify(preferencesFromForm()),
    });
    showMessage("Preferences saved.", "success");
    await loadExistingUser();
  } catch (error) { showMessage(error.message); }
}
async function switchMode(mode) {
  state.mode = mode; clearMessage(); resetPreview(); clearTaskForm();
  const scenarioMode = mode === "product_scenario";
  $("user-field").hidden = scenarioMode;
  $("scenario-field").hidden = !scenarioMode;
  $("save-preferences").hidden = scenarioMode;
  $("scenario-preference-note").hidden = !scenarioMode;
  $("expected-observations").hidden = true;
  state.busy = []; renderBusy();
  if (scenarioMode) {
    state.tasks = []; renderTasks();
    state.timezone = "UTC"; $("timezone").textContent = "Select a scenario";
    $("stored-preferences").textContent = "Temporary scenario values";
  } else await loadExistingUser();
}
async function init() {
  buildWorkingHours();
  problems.forEach((problem) => {
    const label = element("label");
    const input = document.createElement("input");
    input.type = "checkbox"; input.value = problem;
    label.append(input, document.createTextNode(problem));
    $("problem-options").append(label);
  });
  document.querySelectorAll("input[name=mode]").forEach((input) =>
    input.addEventListener("change", () => switchMode(input.value)));
  $("user-select").addEventListener("change", loadExistingUser);
  $("scenario-select").addEventListener("change", loadScenario);
  $("save-preferences").addEventListener("click", savePreferences);
  $("task-form").addEventListener("submit", saveTask);
  $("task-reset").addEventListener("click", clearTaskForm);
  $("busy-form").addEventListener("submit", addBusy);
  $("generate").addEventListener("click", generatePreview);
  $("export-review").addEventListener("click", exportReview);
  try { await Promise.all([loadUsers(), loadScenarios()]); }
  catch (error) { showMessage(error.message); }
}
init();
