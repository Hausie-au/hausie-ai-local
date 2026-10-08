import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import vm from "node:vm";

const controls = new Map();
const element = id => {
  if (!controls.has(id)) controls.set(id, {value: "", hidden: true, classList: {add() {}}, addEventListener() {}});
  return controls.get(id);
};
const sandbox = {
  document: {
    body: {dataset: {page: "inventory"}},
    getElementById: element,
    querySelector: () => element("nav"),
    querySelectorAll: () => []
  },
  location: {search: "", href: "http://localhost/ui/inventory"},
  URLSearchParams,
  URL,
  fetch: () => new Promise(() => {}),
  setInterval() {}
};
vm.createContext(sandbox);
vm.runInContext(readFileSync(new URL("../app/web/app.js", import.meta.url), "utf8"), sandbox);

const entities = [
  {entity_id: "sensor.office_temperature", name: "Office temperature", domain: "sensor", area_name: "Office", state: "22.5", device_class: "temperature", device_id: "device-1", device_name: "Climate sensor", roles: ["environmental_input"], labels: ["climate"], safety: {classification: "observed_only"}},
  {entity_id: "automation.office_lights", name: "Office lights", domain: "automation", area_name: "Office", state: "on", device_class: null, device_id: null, roles: ["observed_only"], labels: [], safety: {classification: "observed_only"}},
  {entity_id: "light.hallway", name: "Hallway light", domain: "light", area_name: "Hallway", state: "off", device_class: null, device_id: "device-2", device_name: "Hallway bulb", roles: ["safe_action_target"], labels: ["lighting"], safety: {classification: "safe_action_target"}},
  {entity_id: "climate.office", name: "Office AC", domain: "climate", area_name: "Office", state: "cool", device_class: null, device_id: "device-1", device_name: "Climate sensor", roles: ["blocked_action_target"], labels: ["climate"], safety: {classification: "blocked_action_target"}},
  {entity_id: "event.hallway_button", name: "Hallway button", domain: "event", area_name: "Hallway", state: "2026-10-08T12:00:00Z", device_class: "button", device_id: "device-3", device_name: "Remote", roles: ["physical_button_input"], labels: ["button"], safety: {classification: "observed_only"}}
];
vm.runInContext("inventoryData = {entities: fixture};", Object.assign(sandbox, {fixture: entities}));
const filtered = () => vm.runInContext("filteredEntities().map(item => item.entity_id)", sandbox);
const clear = () => {
  for (const item of controls.values()) item.value = "";
};

test("area, domain, class, role and label filters combine", () => {
  clear();
  element("filter-area").value = "Office";
  element("filter-domain").value = "sensor";
  element("filter-class").value = "temperature";
  element("filter-role").value = "environmental_input";
  element("filter-label").value = "climate";
  assert.deepEqual(Array.from(filtered()), ["sensor.office_temperature"]);
});

test("state and safety filters work with categories", () => {
  clear();
  element("filter-state").value = "off";
  element("filter-safety").value = "safe_action_target";
  assert.deepEqual(Array.from(filtered()), ["light.hallway"]);
  clear();
  const groups = vm.runInContext("inventoryGroups(inventoryData.entities)", sandbox);
  assert.equal(groups.devices.length, 3);
  assert.equal(groups.automations.length, 1);
  assert.equal(groups.sensors.length, 1);
  assert.equal(groups.targets.length, 2);
  assert.equal(groups.buttons.length, 1);
});

test("inventory renders only the selected category and filtered entities", () => {
  clear();
  element("filter-area").value = "Office";
  vm.runInContext("currentView = 'sensors'; renderInventory();", sandbox);
  assert.match(element("inventory-results").innerHTML, /sensor\.office_temperature/);
  assert.doesNotMatch(element("inventory-results").innerHTML, /light\.hallway/);
  clear();
  vm.runInContext("currentView = 'devices'; renderInventory();", sandbox);
  assert.match(element("inventory-results").innerHTML, /Climate sensor/);
  assert.match(element("inventory-results").innerHTML, /Hallway bulb/);
  vm.runInContext("currentView = 'buttons'; renderInventory();", sandbox);
  assert.match(element("inventory-results").innerHTML, /event\.hallway_button/);
  assert.doesNotMatch(element("inventory-results").innerHTML, /light\.hallway/);
  assert.equal(vm.runInContext("escapeHtml('<img>')", sandbox), "&lt;img&gt;");
});
