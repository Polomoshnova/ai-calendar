const COLLECTIONS = [
  "today",
  "upcoming",
  "backlog",
  "needs_attention",
  "proposed_plans",
  "confirmed_not_applied",
];

export class PlannerApiError extends Error {
  constructor(message, { status = null, kind = "api" } = {}) {
    super(message);
    this.name = "PlannerApiError";
    this.status = status;
    this.kind = kind;
  }
}

async function getJson(url, fetchImpl) {
  let response;
  try {
    response = await fetchImpl(url, { method: "GET", headers: { Accept: "application/json" } });
  } catch (_error) {
    throw new PlannerApiError("Could not reach the Planner API.", { kind: "network" });
  }
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch (_error) {
      // The HTTP status remains the useful error when the body is not JSON.
    }
    const message = response.status === 404
      ? "The selected development user was not found."
      : response.status === 422
        ? `The Planner request was invalid${detail ? `: ${detail}` : "."}`
        : `Planner API request failed (${response.status})${detail ? `: ${detail}` : "."}`;
    throw new PlannerApiError(message, { status: response.status });
  }
  try {
    return await response.json();
  } catch (_error) {
    throw new PlannerApiError("The Planner API returned invalid JSON.", { kind: "malformed" });
  }
}

function validatePlanner(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PlannerApiError("The Planner API returned an unexpected response.", { kind: "malformed" });
  }
  if (typeof value.generated_at !== "string" || typeof value.timezone !== "string") {
    throw new PlannerApiError("The Planner API response is missing metadata.", { kind: "malformed" });
  }
  for (const name of COLLECTIONS) {
    if (!Array.isArray(value[name])) {
      throw new PlannerApiError(`The Planner API response is missing ${name}.`, { kind: "malformed" });
    }
  }
  return value;
}

export async function getPlanner(userId, { days = 14, fetchImpl = fetch } = {}) {
  const query = new URLSearchParams({ user_id: userId, days: String(days) });
  const value = await getJson(`/internal/api/planner?${query}`, fetchImpl);
  return validatePlanner(value);
}

export async function getInternalUsers({ fetchImpl = fetch } = {}) {
  const users = await getJson("/internal/api/users", fetchImpl);
  if (!Array.isArray(users)) {
    throw new PlannerApiError("The users API returned an unexpected response.", { kind: "malformed" });
  }
  return users;
}
