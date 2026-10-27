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

test("log page filters, escapes messages and builds a matching download link", async () => {
  const nodes = new Map();
  const requests = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      value: "", hidden: true, disabled: false, innerHTML: "", href: "",
      classList: {add() {}, toggle() {}},
      events: {}, addEventListener(name, callback) { this.events[name] = callback; }
    });
    return nodes.get(id);
  };
  const context = {
    document: {body: {dataset: {page: "logs"}}, getElementById: node,
      querySelector: () => node("nav"), querySelectorAll: () => []},
    location: {search: "", href: "http://localhost/ui/logs"},
    URLSearchParams, URL, setInterval() {},
    fetch: async path => {
      requests.push(String(path));
      return {ok: true, json: async () => path === "api/v1/status"
        ? {home_assistant_connected: true, mode: "observe-and-suggest"}
        : {entries: [{time: "2026-10-28T00:00:00+00:00", level: "INFO", category: "EVENT",
            logger: "hausie_ai.test", message: "<script>alert(1)</script>"}],
          retained_bytes: 1024, max_bytes: 20971520, has_more: false}};
    }
  };
  vm.createContext(context);
  vm.runInContext(readFileSync(new URL("../app/web/app.js", import.meta.url), "utf8"), context);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(node("log-rows").innerHTML, /&lt;script&gt;/);
  assert.doesNotMatch(node("log-rows").innerHTML, /<script>/);
  assert.equal(node("log-next").disabled, true);
  node("log-level").value = "INFO";
  node("log-search").value = "light.test";
  node("log-apply").events.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(requests.at(-1), /level=INFO/);
  assert.match(requests.at(-1), /search=light.test/);
  assert.match(node("log-download").href, /search=light.test/);
  assert.doesNotMatch(node("log-download").href, /offset=/);
});
