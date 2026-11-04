const MINUTE = 60_000;

function epoch(value, name) {
  const result = new Date(value).getTime();
  if (!Number.isFinite(result)) throw new TypeError(`Invalid ${name}`);
  return result;
}

function formatDateParts(date) {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

export function shiftCalendarDate(value, days) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) throw new TypeError("Invalid calendar date");
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  date.setUTCDate(date.getUTCDate() + days);
  return formatDateParts(date);
}

export function todayInTimezone(timezone, now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function formatSelectedDate(value, timezone = "UTC") {
  const instant = new Date(`${value}T12:00:00Z`);
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(instant);
}

export function timelineTicks(dayStart, dayEnd, timezone) {
  const start = epoch(dayStart, "day_start");
  const end = epoch(dayEnd, "day_end");
  const formatter = new Intl.DateTimeFormat(undefined, {
    timeZone: timezone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZoneName: "short",
  });
  const ticks = [];
  for (let value = start; value <= end; value += 60 * MINUTE) {
    ticks.push({
      instant: new Date(value).toISOString(),
      elapsedMinutes: (value - start) / MINUTE,
      label: formatter.format(new Date(value)),
    });
  }
  if (ticks.at(-1)?.instant !== new Date(end).toISOString()) {
    ticks.push({ instant: new Date(end).toISOString(), elapsedMinutes: (end - start) / MINUTE, label: formatter.format(new Date(end)) });
  }
  return ticks;
}

function assignGroupColumns(group) {
  const columnEnds = [];
  for (const event of group) {
    let column = columnEnds.findIndex((end) => end <= event.clippedStartMs);
    if (column === -1) column = columnEnds.length;
    columnEnds[column] = event.clippedEndMs;
    event.column = column;
  }
  const columns = Math.max(1, columnEnds.length);
  for (const event of group) event.columns = columns;
}

export function layoutTimedEvents(items, dayStart, dayEnd) {
  const start = epoch(dayStart, "day_start");
  const end = epoch(dayEnd, "day_end");
  if (end <= start) throw new RangeError("day_end must be after day_start");
  const duration = end - start;
  const events = items
    .filter((item) => !item.all_day && item.kind !== "needs_attention")
    .map((item) => {
      const actualStart = epoch(item.start, "event start");
      const actualEnd = epoch(item.end, "event end");
      const clippedStartMs = Math.max(start, actualStart);
      const clippedEndMs = Math.min(end, actualEnd);
      return {
        ...item,
        actualStartMs: actualStart,
        actualEndMs: actualEnd,
        clippedStartMs,
        clippedEndMs,
        continuesBefore: actualStart < start,
        continuesAfter: actualEnd > end,
      };
    })
    .filter((item) => item.clippedEndMs > item.clippedStartMs)
    .sort((left, right) => left.clippedStartMs - right.clippedStartMs || left.clippedEndMs - right.clippedEndMs || String(left.id).localeCompare(String(right.id)));

  let group = [];
  let groupEnd = -Infinity;
  const flush = () => {
    if (group.length) assignGroupColumns(group);
    group = [];
    groupEnd = -Infinity;
  };
  for (const event of events) {
    if (group.length && event.clippedStartMs >= groupEnd) flush();
    group.push(event);
    groupEnd = Math.max(groupEnd, event.clippedEndMs);
  }
  flush();

  return events.map((event) => ({
    ...event,
    topPercent: ((event.clippedStartMs - start) / duration) * 100,
    heightPercent: ((event.clippedEndMs - event.clippedStartMs) / duration) * 100,
    elapsedMinutes: (event.clippedEndMs - event.clippedStartMs) / MINUTE,
  }));
}
