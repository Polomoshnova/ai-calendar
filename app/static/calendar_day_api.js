export class CalendarDayApiError extends Error {
  constructor(message, { status = null, kind = "api" } = {}) {
    super(message);
    this.name = "CalendarDayApiError";
    this.status = status;
    this.kind = kind;
  }
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function validateCalendarDay(value) {
  if (!isObject(value)) {
    throw new CalendarDayApiError("The Calendar API returned an unexpected response.", { kind: "malformed" });
  }
  for (const field of ["date", "timezone", "day_start", "day_end", "generated_at"]) {
    if (typeof value[field] !== "string") {
      throw new CalendarDayApiError(`The Calendar API response is missing ${field}.`, { kind: "malformed" });
    }
  }
  if (!isObject(value.sync_state) || typeof value.sync_state.status !== "string" || !Array.isArray(value.sync_state.errors)) {
    throw new CalendarDayApiError("The Calendar API response is missing sync state.", { kind: "malformed" });
  }
  if (!Array.isArray(value.warnings) || !Array.isArray(value.items)) {
    throw new CalendarDayApiError("The Calendar API response is missing calendar items.", { kind: "malformed" });
  }
  return value;
}

export async function getCalendarDay(date, { fetchImpl = fetch, signal } = {}) {
  const query = new URLSearchParams({ date });
  let response;
  try {
    response = await fetchImpl(`/api/v1/calendar/day?${query}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new CalendarDayApiError("Could not reach the Calendar API.", { kind: "network" });
  }
  if (!response.ok) {
    const message = response.status === 404
      ? "Calendar Day is not available in this environment."
      : response.status === 422
        ? "The selected calendar date is invalid."
        : `Calendar API request failed (${response.status}).`;
    throw new CalendarDayApiError(message, { status: response.status });
  }
  try {
    return validateCalendarDay(await response.json());
  } catch (error) {
    if (error instanceof CalendarDayApiError) throw error;
    throw new CalendarDayApiError("The Calendar API returned invalid JSON.", { kind: "malformed" });
  }
}
