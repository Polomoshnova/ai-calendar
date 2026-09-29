import { getInternalUsers, getPlanner } from "/internal/static/planner_api.js";
import { isEmptyPlanner, renderEmpty, renderError, renderLoading, renderPlanner } from "/internal/static/planner_view.js";

const userSelect = document.querySelector("#user-select");
const status = document.querySelector("#planner-status");
const content = document.querySelector("#planner-content");

function showStatus(markup, error = false) {
  content.hidden = true;
  status.hidden = false;
  status.classList.toggle("status-error", error);
  status.innerHTML = markup;
}

function showContent(markup) {
  status.hidden = true;
  content.hidden = false;
  content.innerHTML = markup;
}

async function loadPlanner() {
  const userId = userSelect.value;
  if (!userId) return;
  showStatus(renderLoading());
  const url = new URL(window.location.href);
  url.searchParams.set("user_id", userId);
  window.history.replaceState({}, "", url);
  try {
    const planner = await getPlanner(userId);
    showContent(isEmptyPlanner(planner) ? renderEmpty(planner) : renderPlanner(planner));
  } catch (error) {
    showStatus(renderError(error instanceof Error ? error.message : "Unexpected error."), true);
    document.querySelector("#retry-planner")?.addEventListener("click", loadPlanner);
  }
}

async function start() {
  try {
    const users = await getInternalUsers();
    if (users.length === 0) {
      userSelect.innerHTML = '<option value="">No development users</option>';
      showStatus(renderError("No development users exist yet."), true);
      return;
    }
    userSelect.replaceChildren(...users.map((user) => {
      const option = document.createElement("option");
      option.value = user.id;
      option.textContent = `${user.email} · ${user.timezone}`;
      return option;
    }));
    const requested = new URLSearchParams(window.location.search).get("user_id");
    if (requested && users.some((user) => user.id === requested)) userSelect.value = requested;
    userSelect.disabled = false;
    userSelect.addEventListener("change", loadPlanner);
    await loadPlanner();
  } catch (error) {
    showStatus(renderError(error instanceof Error ? error.message : "Unexpected error."), true);
  }
}

start();
