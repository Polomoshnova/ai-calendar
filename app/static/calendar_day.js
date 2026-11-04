import { getCalendarDay } from "/internal/static/calendar_day_api.js";
import { shiftCalendarDate, todayInTimezone } from "/internal/static/calendar_day_layout.js";
import { renderCalendarDay, renderLoading, renderRequestError, selectedDateHeading } from "/internal/static/calendar_day_view.js";

const status = document.querySelector("#calendar-status");
const content = document.querySelector("#calendar-content");
const heading = document.querySelector("#selected-date");
let timezone = document.body.dataset.timezone;
let selectedDate = new URLSearchParams(window.location.search).get("date") || document.body.dataset.initialDate;
let activeRequest = null;
let requestSequence = 0;

function setHeading() {
  heading.textContent = selectedDateHeading(selectedDate, timezone);
}

function showStatus(markup, error = false) {
  content.hidden = true;
  content.replaceChildren();
  status.hidden = false;
  status.classList.toggle("status-error", error);
  status.innerHTML = markup;
}

function showContent(markup) {
  status.hidden = true;
  content.hidden = false;
  content.innerHTML = markup;
  document.querySelector("#retry-calendar")?.addEventListener("click", loadDay);
}

function updateUrl() {
  const url = new URL(window.location.href);
  url.searchParams.set("date", selectedDate);
  window.history.replaceState({}, "", url);
}

async function loadDay() {
  activeRequest?.abort();
  activeRequest = new AbortController();
  const sequence = ++requestSequence;
  showStatus(renderLoading());
  setHeading();
  try {
    const day = await getCalendarDay(selectedDate, { signal: activeRequest.signal });
    if (sequence !== requestSequence) return;
    timezone = day.timezone;
    selectedDate = day.date;
    setHeading();
    updateUrl();
    showContent(renderCalendarDay(day));
  } catch (error) {
    if (error?.name === "AbortError" || sequence !== requestSequence) return;
    showStatus(renderRequestError(), true);
    document.querySelector("#retry-calendar")?.addEventListener("click", loadDay);
  }
}

function navigate(days) {
  selectedDate = shiftCalendarDate(selectedDate, days);
  loadDay();
}

document.querySelector("#previous-day").addEventListener("click", () => navigate(-1));
document.querySelector("#next-day").addEventListener("click", () => navigate(1));
document.querySelector("#today").addEventListener("click", () => {
  selectedDate = todayInTimezone(timezone);
  loadDay();
});

setHeading();
loadDay();
