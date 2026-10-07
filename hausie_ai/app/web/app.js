"use strict";

const pageName = document.body.dataset.page;
const pageElement = document.getElementById(pageName);
if (pageElement) pageElement.hidden = false;
document.querySelector(`[data-nav="${pageName}"]`)?.classList.add("active");

const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[character]);
const label = value => String(value ?? "").replaceAll("_", " ");
const metric = (value, title) => `<div class="metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(title)}</span></div>`;
const roles = values => (values || []).map(value => `<span class="tag">${escapeHtml(label(value))}</span>`).join("");
const decimal = value => value == null ? "Pending" : Number(value).toFixed(2);

async function getJson(path) {
  const response = await fetch(path, {cache: "no-store"});
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function showError(error) {
  const notice = document.getElementById("error");
  notice.textContent = `Unable to load this page: ${error.message || error}`;
  notice.hidden = false;
}

function updateStatus(status) {
  const connection = document.getElementById("connection");
  connection.textContent = status.home_assistant_connected ? "Home Assistant connected" : "Waiting for Home Assistant data";
  const mode = document.getElementById("mode");
  mode.textContent = status.mode === "auto-act" ? "Auto-act enabled" : "Observe & suggest";
  mode.classList.toggle("warn", status.mode === "auto-act");
}

function entityRows(items) {
  return items.map(item => `<tr>
    <td><strong>${escapeHtml(item.name)}</strong><br><small>${escapeHtml(item.entity_id)}</small></td>
    <td>${escapeHtml(item.area_name || "Unassigned")}</td>
    <td>${escapeHtml(item.state)} ${escapeHtml(item.unit || "")}${item.normalized_value ? `<br><small>band: ${escapeHtml(item.normalized_value)}</small>` : ""}</td>
    <td>${roles(item.roles)}</td>
    <td>${escapeHtml(item.safety?.reason || "—")}</td>
  </tr>`).join("");
}

function entityTable(items) {
  return `<div class="table-wrap"><table><thead><tr><th>Entity</th><th>Area</th><th>Current value</th><th>Classification</th><th>Safety</th></tr></thead><tbody>${entityRows(items)}</tbody></table></div>`;
}

const sensorDomains = new Set(["sensor", "binary_sensor", "weather", "person", "device_tracker"]);
const validViews = new Set(["devices", "sensors", "automations", "targets", "other", "all"]);
const explanations = {
  devices: "Grouped by Home Assistant device. Open a device to see all its entities and their current values.",
  sensors: "Environmental readings, presence and other sensor entities. Only classified inputs contribute to the model's context.",
  automations: "Home Assistant automation entities. Their state is visible, but existing automations are not automatically used as training examples.",
  targets: "Potential control targets. The safety column explains what Hausie AI is allowed to suggest or why it is blocked.",
  other: "Entities that are not in the sensor, automation or action-target categories.",
  all: "Every state entity Hausie AI currently receives from Home Assistant."
};

let inventoryData = {entities: [], summary: {}, registry_available: false};
let currentView = validViews.has(new URLSearchParams(location.search).get("view")) ? new URLSearchParams(location.search).get("view") : "devices";
let pageIndex = 0;
const pageSize = 40;

function inventoryGroups(entities) {
  const devices = new Map();
  for (const entity of entities) {
    if (!entity.device_id) continue;
    if (!devices.has(entity.device_id)) devices.set(entity.device_id, {
      id: entity.device_id, name: entity.device_name || entity.device_id,
      area: entity.area_name || "Unassigned", entities: []
    });
    devices.get(entity.device_id).entities.push(entity);
  }
  const isTarget = item => (item.roles || []).some(role => role === "safe_action_target" || role === "blocked_action_target");
  const isSensor = item => sensorDomains.has(item.domain);
  const isAutomation = item => item.domain === "automation";
  return {
    devices: Array.from(devices.values()).sort((a, b) => a.name.localeCompare(b.name)),
    sensors: entities.filter(isSensor),
    automations: entities.filter(isAutomation),
    targets: entities.filter(isTarget),
    other: entities.filter(item => !isSensor(item) && !isAutomation(item) && !isTarget(item)),
    all: entities
  };
}

function populateAttributeFilters() {
  const entities = inventoryData.entities || [];
  const specifications = {
    "filter-area": ["Any area", item => [item.area_name]],
    "filter-domain": ["Any domain", item => [item.domain]],
    "filter-class": ["Any class", item => [item.device_class]],
    "filter-role": ["Any role", item => item.roles || []],
    "filter-safety": ["Any classification", item => [item.safety?.classification]],
    "filter-label": ["Any label", item => item.labels || []]
  };
  for (const [id, [placeholder, valuesFor]] of Object.entries(specifications)) {
    const select = document.getElementById(id);
    const previous = select.value;
    const values = [...new Set(entities.flatMap(valuesFor).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b)));
    select.replaceChildren(new Option(placeholder, ""), ...values.map(value => new Option(label(value), value)));
    if (values.includes(previous)) select.value = previous;
  }
}

function filteredEntities() {
  const filters = {
    area: document.getElementById("filter-area").value,
    domain: document.getElementById("filter-domain").value,
    state: document.getElementById("filter-state").value.trim().toLocaleLowerCase(),
    deviceClass: document.getElementById("filter-class").value,
    role: document.getElementById("filter-role").value,
    safety: document.getElementById("filter-safety").value,
    label: document.getElementById("filter-label").value,
    query: document.getElementById("inventory-search").value.trim().toLocaleLowerCase()
  };
  return (inventoryData.entities || []).filter(item => {
    if (filters.area && item.area_name !== filters.area) return false;
    if (filters.domain && item.domain !== filters.domain) return false;
    if (filters.state && !String(item.state ?? "").toLocaleLowerCase().includes(filters.state)) return false;
    if (filters.deviceClass && item.device_class !== filters.deviceClass) return false;
    if (filters.role && !(item.roles || []).includes(filters.role)) return false;
    if (filters.safety && item.safety?.classification !== filters.safety) return false;
    if (filters.label && !(item.labels || []).includes(filters.label)) return false;
    if (filters.query) {
      const fields = [item.name, item.entity_id, item.area_name, item.device_name, item.device_id, item.state, item.device_class, ...(item.labels || [])];
      if (!fields.some(field => String(field ?? "").toLocaleLowerCase().includes(filters.query))) return false;
    }
    return true;
  });
}

function renderInventory() {
  const groups = inventoryGroups(filteredEntities());
  for (const [view, items] of Object.entries(groups)) {
    const count = document.querySelector(`[data-count="${view}"]`);
    if (count) count.textContent = `(${items.length})`;
  }
  document.querySelectorAll("#inventory-tabs button").forEach(button => {
    const selected = button.dataset.view === currentView;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  document.getElementById("inventory-explanation").textContent = explanations[currentView];
  const items = groups[currentView];
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  pageIndex = Math.min(pageIndex, totalPages - 1);
  const visible = items.slice(pageIndex * pageSize, (pageIndex + 1) * pageSize);
  document.getElementById("inventory-count").textContent = `${items.length} ${currentView === "devices" ? "devices" : "entities"}`;
  document.getElementById("page-indicator").textContent = `Page ${pageIndex + 1} of ${totalPages}`;
  document.getElementById("previous-page").disabled = pageIndex === 0;
  document.getElementById("next-page").disabled = pageIndex >= totalPages - 1;
  const results = document.getElementById("inventory-results");
  if (!items.length) {
    const filtering = [...document.querySelectorAll(".filters select, .filters input, #inventory-search")].some(control => control.value);
    results.innerHTML = `<div class="empty">${filtering ? "No matching items in this category." : "No items in this category yet."}</div>`;
  } else if (currentView === "devices") {
    results.innerHTML = `<div class="device-list">${visible.map(device => {
      const input = device.entities.some(entity => (entity.roles || []).some(role => role === "environmental_input" || role === "context_input"));
      const action = device.entities.some(entity => (entity.roles || []).includes("safe_action_target"));
      const role = input && action ? "Input + action" : input ? "Input" : action ? "Action target" : "Observed only";
      return `<details class="device"><summary><span><strong>${escapeHtml(device.name)}</strong><small>${escapeHtml(device.area)} · ${device.entities.length} entities · ${escapeHtml(role)}</small></span></summary>${entityTable(device.entities)}</details>`;
    }).join("")}</div>`;
  } else {
    results.innerHTML = entityTable(visible);
  }
}

function prepareInventoryControls() {
  document.getElementById("inventory-tabs").addEventListener("click", event => {
    const button = event.target.closest("button[data-view]");
    if (!button) return;
    currentView = button.dataset.view;
    pageIndex = 0;
    const url = new URL(location.href);
    url.searchParams.set("view", currentView);
    history.replaceState(null, "", url);
    renderInventory();
  });
  document.getElementById("inventory-search").addEventListener("input", () => { pageIndex = 0; renderInventory(); });
  document.querySelectorAll(".filters select, .filters input").forEach(control => {
    control.addEventListener(control.tagName === "SELECT" ? "change" : "input", () => { pageIndex = 0; renderInventory(); });
  });
  document.getElementById("clear-filters").addEventListener("click", () => {
    document.getElementById("inventory-search").value = "";
    document.querySelectorAll(".filters select, .filters input").forEach(control => { control.value = ""; });
    pageIndex = 0;
    renderInventory();
  });
  document.getElementById("previous-page").addEventListener("click", () => { pageIndex--; renderInventory(); });
  document.getElementById("next-page").addEventListener("click", () => { pageIndex++; renderInventory(); });
}

async function loadPage() {
  const status = await getJson("api/v1/status");
  updateStatus(status);
  if (pageName === "overview") {
    const inventory = await getJson("api/v1/inventory");
    document.getElementById("summary").innerHTML = [
      metric(inventory.summary.entities, "Entities visible"),
      metric(inventory.summary.environmental_inputs, "Environmental inputs"),
      metric(inventory.summary.safe_action_targets, "Safe action targets"),
      metric(status.stats?.observations ?? 0, "Learning observations")
    ].join("");
    document.getElementById("context").textContent = JSON.stringify(status.current_context || {status: "Waiting for the first snapshot"}, null, 2);
  } else if (pageName === "inventory") {
    inventoryData = await getJson("api/v1/inventory");
    if (!new URLSearchParams(location.search).has("view") && inventoryGroups(inventoryData.entities || []).devices.length === 0) currentView = "all";
    populateAttributeFilters();
    document.getElementById("inventory-summary").innerHTML = [
      metric(inventoryData.summary.entities, "Entities"),
      metric(inventoryData.summary.environmental_inputs, "Environmental inputs"),
      metric(inventoryData.summary.safe_action_targets, "Safe action targets"),
      metric(inventoryData.summary.blocked_action_targets, "Blocked targets")
    ].join("");
    document.getElementById("registry-note").hidden = inventoryData.registry_available;
    renderInventory();
  } else if (pageName === "activity") {
    const events = await getJson("api/v1/environment/events?limit=100");
    document.getElementById("events").innerHTML = events.map(item => `<tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.entity_id)}</td><td>${escapeHtml(item.area_name || "Unassigned")}</td><td>${escapeHtml(item.old_state)} → ${escapeHtml(item.new_state)}</td><td>${escapeHtml(item.normalized_value || "")}</td></tr>`).join("") || '<tr><td colspan="5">No environmental or occupancy changes recorded yet.</td></tr>';
  } else if (pageName === "decisions") {
    const decisions = await getJson("api/v1/decisions?limit=100");
    document.getElementById("decisions-table").innerHTML = decisions.map(item => `<tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.decision)} (${escapeHtml(Number(item.confidence).toFixed(2))})</td><td>${escapeHtml(item.action ? `${item.action.domain}.${item.action.service} ${item.action.entity_id}` : "—")}</td><td>${escapeHtml(item.reason)}</td><td>${item.rated ? "Rated" : item.decision === "SUGGEST_ACTION" ? `<button type="button" class="feedback-button" data-decision="${Number(item.id)}" data-reward="1">Helpful</button> <button type="button" class="feedback-button" data-decision="${Number(item.id)}" data-reward="-1">Not helpful</button>` : "—"}</td></tr>`).join("") || '<tr><td colspan="5">No decisions recorded yet.</td></tr>';
  } else if (pageName === "learning") {
    const lab = await getJson("api/v1/learning/lab");
    const outcomes = await getJson("api/v1/learning/outcomes?limit=30");
    const report = lab.actions;
    const methodNames = Object.fromEntries(lab.catalog.map(item => [item.id, item.name]));
    const totals = report.methods || [];
    document.querySelectorAll("#learning-tabs button[data-family]").forEach(button => {
      const count = lab.catalog.filter(item => item.family === button.dataset.family).length;
      button.textContent = `${label(button.dataset.family)} (${count})`;
    });
    document.getElementById("learning-summary").innerHTML = [
      metric(totals[0]?.opportunities ?? 0, "Event opportunities"),
      metric(totals[0]?.labelled ?? 0, "With a user action"),
      metric(lab.catalog.length, "Methods available locally")
    ].join("");
    document.getElementById("learning-methods").innerHTML = lab.catalog.filter(item => item.family === "actions").map(item => {
      const row = totals.find(value => value.method === item.id) || {};
      return `<tr><td><strong>${escapeHtml(item.name)}</strong><br><small>${escapeHtml(item.status)}</small></td><td>${escapeHtml(row.opportunities ?? 0)}</td><td>${escapeHtml(row.predicted ?? 0)}</td><td>${escapeHtml(row.labelled ?? 0)}</td><td>${escapeHtml(row.matched ?? 0)}</td><td>${escapeHtml(row.disagreed ?? 0)}</td></tr>`;
    }).join("");
    document.getElementById("learning-predictions").innerHTML = (report.recent || []).map(item => `<tr><td>${escapeHtml(item.created_at)}<br><small>${escapeHtml(item.trigger.area_name || item.trigger.area)}: ${escapeHtml(item.trigger.kind)} ${escapeHtml(item.trigger.direction)} (${escapeHtml(item.trigger.old_band)} → ${escapeHtml(item.trigger.new_band)})</small></td><td>${escapeHtml(methodNames[item.method] || item.method)}</td><td>${escapeHtml(item.action ? `${item.action.domain}.${item.action.service} ${item.action.entity_id}` : "No prediction")}</td><td>${escapeHtml(item.actual_action ? `${item.actual_action.domain}.${item.actual_action.service} ${item.actual_action.entity_id}` : "Not observed")}</td><td>${escapeHtml(item.reason)}</td></tr>`).join("") || '<tr><td colspan="5">No event predictions yet.</td></tr>';
    document.getElementById("learning-outcomes").innerHTML = outcomes.map(item => {
      const before = item.before || {};
      const after = item.after || {};
      const readings = Object.keys(before).slice(0, 12).map(entity => `<div><small>${escapeHtml(entity)}:</small> ${escapeHtml(before[entity].value)} → ${escapeHtml(after[entity]?.value ?? "pending")}</div>`).join("");
      return `<tr><td>${escapeHtml(item.created_at)}<br><small>${escapeHtml(item.area)}</small></td><td>${escapeHtml(`${item.action.domain}.${item.action.service} ${item.action.entity_id}`)}</td><td>${readings || "No area sensors"}</td><td>${item.after ? escapeHtml(item.other_user_actions) : "Pending 30-minute window"}</td></tr>`;
    }).join("") || '<tr><td colspan="4">No manual actions with an assigned area yet.</td></tr>';
    document.getElementById("learning-sensors").innerHTML = lab.catalog.filter(item => item.family === "sensors").flatMap(item => {
      const rows = lab.sensors.filter(row => row.method === item.id);
      return (rows.length ? rows : [{method: item.id, kind: "Waiting for data", unit: "", opportunities: 0, evaluated: 0}]);
    }).map(item => `<tr><td>${escapeHtml(methodNames[item.method] || item.method)}</td><td>${escapeHtml(item.kind)} ${escapeHtml(item.unit)}</td><td>${escapeHtml(item.opportunities)}</td><td>${escapeHtml(item.evaluated)}</td><td>${escapeHtml(decimal(item.mean_absolute_error))}</td></tr>`).join("");
    document.getElementById("learning-responses").innerHTML = lab.catalog.filter(item => item.family === "responses").flatMap(item => {
      const rows = lab.responses.filter(row => row.method === item.id);
      return (rows.length ? rows : [{method: item.id, entity_id: "Waiting for data", predictions: 0, evaluated: 0}]);
    }).map(item => `<tr><td>${escapeHtml(methodNames[item.method] || item.method)}</td><td>${escapeHtml(item.entity_id)}</td><td>${escapeHtml(item.predictions)}</td><td>${escapeHtml(item.evaluated)}</td><td>${escapeHtml(decimal(item.mean_absolute_error))}</td></tr>`).join("");
    document.getElementById("learning-preferences").innerHTML = [
      metric(lab.preferences.methods.reduce((sum, row) => sum + row.predictions, 0), "Model predictions"),
      metric(lab.preferences.methods.reduce((sum, row) => sum + row.evaluated, 0), "Explicit ratings scored")
    ].join("");
    document.getElementById("learning-preference-methods").innerHTML = lab.catalog.filter(item => item.family === "preferences").map(item => {
      const row = lab.preferences.methods.find(value => value.method === item.id) || {};
      return `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(row.predictions ?? 0)}</td><td>${escapeHtml(row.evaluated ?? 0)}</td><td>${escapeHtml(decimal(row.brier_score))}</td></tr>`;
    }).join("");
    const anomaly = lab.anomalies.summary;
    document.getElementById("learning-anomaly-summary").innerHTML = [
      metric(anomaly.opportunities ?? 0, "Changes seen"), metric(anomaly.scored, "Model scores"),
      metric(anomaly.flagged ?? 0, "Flagged"), metric(anomaly.reviewed, "Reviewed"),
      metric(anomaly.agreed ?? 0, "Agreed with your review")
    ].join("");
    document.getElementById("learning-anomaly-methods").innerHTML = lab.catalog.filter(item => item.family === "anomalies").map(item => {
      const row = lab.anomalies.methods.find(value => value.method === item.id) || {};
      return `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(row.opportunities ?? 0)}</td><td>${escapeHtml(row.scored ?? 0)}</td><td>${escapeHtml(row.flagged ?? 0)}</td><td>${escapeHtml(row.reviewed ?? 0)}</td><td>${escapeHtml(row.agreed ?? 0)}</td></tr>`;
    }).join("");
    document.getElementById("learning-anomalies").innerHTML = lab.anomalies.recent.map(item => `<tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(methodNames[item.method] || item.method)}<br><small>${escapeHtml(item.entity_id)}</small></td><td>${escapeHtml(decimal(item.score))}</td><td>${escapeHtml(item.explanation)}</td><td>${item.reviewed == null ? `<button type="button" class="feedback-button" data-anomaly="${Number(item.id)}" data-surprising="1">Surprising</button> <button type="button" class="feedback-button" data-anomaly="${Number(item.id)}" data-surprising="0">Expected</button>` : item.reviewed ? "Surprising" : "Expected"}</td></tr>`).join("") || '<tr><td colspan="5">No unusual changes flagged yet.</td></tr>';
    document.getElementById("learning-timing").innerHTML = lab.catalog.filter(item => item.family === "timing").map(item => {
      const row = lab.timing.find(value => value.method === item.id) || {};
      return `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(row.opportunities ?? 0)}</td><td>${escapeHtml(row.predicted ?? 0)}</td><td>${escapeHtml(row.labelled ?? 0)}</td><td>${escapeHtml(row.evaluated ?? 0)}</td><td>${escapeHtml(decimal(row.mean_absolute_error_seconds))}</td></tr>`;
    }).join("");
    document.getElementById("learning-routines").innerHTML = lab.catalog.filter(item => item.family === "routines").map(item => {
      const row = lab.routines.find(value => value.method === item.id) || {};
      return `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(row.opportunities ?? 0)}</td><td>${escapeHtml(row.predicted ?? 0)}</td><td>${escapeHtml(row.labelled ?? 0)}</td><td>${escapeHtml(row.matched ?? 0)}</td><td>${escapeHtml(row.disagreed ?? 0)}</td></tr>`;
    }).join("");
  }
  document.getElementById("error").hidden = true;
}

if (pageName === "inventory") prepareInventoryControls();
if (pageName === "learning") {
  const families = new Set(["actions", "sensors", "responses", "preferences", "anomalies", "timing", "routines"]);
  const selectFamily = family => {
    document.querySelectorAll("#learning-tabs button").forEach(button => {
      const selected = button.dataset.family === family;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    document.querySelectorAll("[data-learning-family]").forEach(section => {
      section.hidden = section.dataset.learningFamily !== family;
    });
  };
  const selected = new URLSearchParams(location.search).get("family");
  selectFamily(families.has(selected) ? selected : "actions");
  document.getElementById("learning-tabs").addEventListener("click", event => {
    const button = event.target.closest("button[data-family]");
    if (!button) return;
    selectFamily(button.dataset.family);
    const url = new URL(location.href);
    url.searchParams.set("family", button.dataset.family);
    history.replaceState(null, "", url);
  });
  document.getElementById("learning-anomalies").addEventListener("click", async event => {
    const button = event.target.closest("button[data-anomaly]");
    if (!button) return;
    button.disabled = true;
    try {
      const response = await fetch(`api/v1/learning/anomalies/${Number(button.dataset.anomaly)}/feedback`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({surprising: button.dataset.surprising === "1"})
      });
      if (!response.ok) throw new Error(`Anomaly review: HTTP ${response.status}`);
      await loadPage();
    } catch (error) { button.disabled = false; showError(error); }
  });
}
if (pageName === "decisions") document.getElementById("decisions-table").addEventListener("click", async event => {
  const button = event.target.closest("button[data-decision]");
  if (!button) return;
  button.disabled = true;
  try {
    const response = await fetch(`api/v1/decisions/${Number(button.dataset.decision)}/feedback`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({reward: Number(button.dataset.reward), source: "explicit_user_feedback"})
    });
    if (!response.ok) throw new Error(`Feedback: HTTP ${response.status}`);
    await loadPage();
  } catch (error) { button.disabled = false; showError(error); }
});
loadPage().catch(showError);
setInterval(() => loadPage().catch(showError), pageName === "learning" ? 60000 : pageName === "inventory" ? 30000 : 15000);
