import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Exercise rendering decisions without a browser or the external Lit CDN.
const source = readFileSync(new URL("../custom_components/climado/frontend/climado-card.js", import.meta.url), "utf8")
  .replace(/import\s*\{[^}]+\}\s*from\s*"[^"]+";/, "");
const classes = new Map();
const flatten = (value) => Array.isArray(value) ? value.map(flatten).join("") : typeof value === "function" ? "" : String(value ?? "");
const template = (strings, ...values) => strings.reduce((text, part, i) => text + part + flatten(values[i]), "");
const context = vm.createContext({
  LitElement: class {}, html: template, css: template,
  customElements: { define: (name, cls) => classes.set(name, cls) },
  window: {}, console: { info() {} },
});
vm.runInContext(source, context);

function card(attributes) {
  const card = new (classes.get("climado-card"))();
  card.setConfig({ entity: "select.climado_mode" });
  const states = {
    "select.climado_mode": { state: "sleep", attributes: {} },
    "sensor.climado_effective_mode": { state: "sleep", attributes },
    "sensor.climado_control_reason": { state: "manual_sleep", attributes: {} },
    "sensor.climado_resolved_target": { state: "unknown", attributes: {} },
  };
  card.hass = { states, entities: Object.fromEntries(Object.keys(states).map(id => [id, { device_id: "test" }])) };
  return card;
}

test("pending Sleep shows an unconfirmed target and waiting status", () => {
  const view = card({ command_pending: true, regulating: "bedroom", hvac_action: "idle" }).render();
  assert.match(view, /Waiting for thermostat confirmation/);
  assert.match(view, /class="target">—</);
  assert.doesNotMatch(view, /command failed/);
});

test("failed command shows retry status", () => {
  const view = card({ command_pending: true, command_error: "offline" }).render();
  assert.match(view, /Thermostat command failed; retrying/);
  assert.doesNotMatch(view, /Waiting for thermostat confirmation/);
});

test("confirmed thermostat state replaces the waiting indicator", () => {
  const item = card({ command_pending: false, regulating: "bedroom", hvac_action: "cooling" });
  item.hass.states["sensor.climado_resolved_target"].state = "23.1";
  const view = item.render();
  assert.match(view, /class="target">23.1°</);
  assert.match(view, /Cooling/);
  assert.doesNotMatch(view, /Waiting for thermostat confirmation/);
});

test("missing equipment state is not represented as idle", () => {
  const item = card({});
  assert.equal(item._hvacInfo(undefined).label, "Unknown");
  assert.equal(item._hvacInfo("idle").label, "Idle");
});
