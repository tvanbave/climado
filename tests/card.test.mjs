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

test("electricity plan discovery survives rename without replacing the comfort selector", async () => {
  const item = card({electricity_plan:"ULO"});
  item.hass.states['select.utility_choice'] = {state:'ULO',attributes:{climado_key:'electricity_plan'}};
  item.hass.entities['select.utility_choice'] = {device_id:'test'};
  item.hass.states['sensor.climado_rate_tier'] = {state:'Mid-peak',attributes:{electricity_plan:'ULO',tier_id:'mid_peak',
    electricity_price:{status:'ready',per_kwh:.157,basis:'energy_only'}}};
  item.hass.entities['sensor.climado_rate_tier'] = {device_id:'test'};
  const e=item._entities();
  assert.equal(e.mode,'select.climado_mode');
  assert.equal(e.electricity_plan,'select.utility_choice');
  assert.match(item.render(),/15.7¢\/kWh/);
  assert.match(item.render(),/Energy only · CAD/);
  let call;
  item.hass.callService=async(...args)=>{call=args;};
  await item._setElectricityPlan(e,'TOU');
  assert.equal(JSON.stringify(call),JSON.stringify(['select','select_option',{entity_id:'select.utility_choice',option:'TOU'}]));
  assert.equal(item.hass.states['select.utility_choice'].state,'ULO');
  item.hass.states['sensor.climado_rate_tier'].attributes.electricity_price={status:'unavailable',per_kwh:null};
  assert.doesNotMatch(item.render(),/15.7¢\/kWh|0.0¢\/kWh/);
});

test("plan control is blocked during handoff and reports failed changes", async () => {
  const item=card({auto_fuel:{phase:'stopping'}});
  item.hass.states['select.climado_electricity_plan']={state:'ULO',attributes:{climado_key:'electricity_plan'}};
  item.hass.entities['select.climado_electricity_plan']={device_id:'test'};
  let called=false;
  item.hass.callService=async()=>{called=true;throw new Error('Could not save plan');};
  await item._setElectricityPlan(item._entities(),'TOU');
  assert.equal(called,false);
  item.hass.states['sensor.climado_effective_mode'].attributes.auto_fuel.phase=null;
  await item._setElectricityPlan(item._entities(),'TOU');
  assert.match(item.render(),/Could not save plan/);
  assert.equal(item._rateBusy,false);
});

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

test("external thermostat preset shows its target without claiming a manual change", () => {
  const item = card({manual_hold_active:true,manual_hold_value:["preset","home"],
    manual_hold_until:"2026-10-22T11:00:00Z"});
  item.hass.states["sensor.climado_effective_mode"].state = "thermostat_preset";
  item.hass.states["sensor.climado_control_reason"].state = "thermostat_preset";
  item.hass.states["sensor.climado_resolved_target"].state = "21";
  const view = item.render();
  assert.match(view, /Thermostat Home/);
  assert.match(view, /Thermostat target/);
  assert.match(view, /Following thermostat preset/);
  assert.match(view, /class="target">21°/);
  assert.doesNotMatch(view, /Manual hold|Respecting your manual change/);
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

test("heating labels do not claim cooling or automatic fuel selection", () => {
  const item = card({ hvac_mode: "heat", hvac_action: "heating", fuel_control: "manual" });
  item.hass.states["sensor.climado_control_reason"].state = "night/ecobee-sleep";
  const view = item.render();
  assert.match(view, /Manual fuel/);
  assert.match(view, /Overnight bedroom comfort/);
  assert.doesNotMatch(view, />AC<|cooling the bedroom/);
});

test("unconfigured heating has an explicit inactive reason", () => {
  const item = card({ hvac_mode: "heat" });
  item.hass.states["sensor.climado_effective_mode"].state = "inactive";
  item.hass.states["sensor.climado_control_reason"].state = "heating_not_enabled";
  assert.match(item.render(), /Heating targets are not enabled/);
});

test("Windows open switch is discovered and shown with paused status", () => {
  const item = card({ windows_open: true, hvac_mode: "off" });
  item.hass.states["switch.climado_windows_open"] = { state: "on", attributes: {} };
  item.hass.entities["switch.climado_windows_open"] = { device_id: "test" };
  item.hass.states["sensor.climado_effective_mode"].state = "windows_open";
  item.hass.states["sensor.climado_control_reason"].state = "windows_open";
  assert.equal(item._entities().windows, "switch.climado_windows_open");
  assert.match(item.render(), /aria-label="Windows open"/);
  assert.match(item.render(), /Heating and cooling paused; fan unchanged/);
});

test("older installs do not show a nonfunctional window toggle", () => {
  assert.doesNotMatch(card({}).render(), /aria-label="Windows open"/);
});

test("automatic windows status is distinct from the manual pause switch", () => {
  const item = card({windows_automatic:true, windows_manual:false, auto_windows:{
    enabled:true, status:"paused", open_names:["Patio Door", "Dining Room Window"],
  }});
  item.hass.states["switch.climado_windows_open"] = {state:"off", attributes:{}};
  item.hass.entities["switch.climado_windows_open"] = {device_id:"test"};
  const view = item.render();
  assert.match(view, /aria-label="Manual windows pause"/);
  assert.match(view, /Auto windows: heating and cooling paused/);
  assert.match(view, /Patio Door, Dining Room Window/);
});

test("auto closure does not promise to clear a manual pause", () => {
  const item = card({});
  const state = {status:"waiting_to_resume",deadline:"2026-10-20T12:02:00-04:00"};
  assert.match(item._windowStatus(state,true,true), /manual pause remains/);
  assert.match(item._windowStatus(state,false,true), /resume at/);
  assert.match(item._windowStatus({status:"sensor_unavailable"},false,true), /pause retained/);
  assert.doesNotMatch(item._windowStatus({status:"sensor_unavailable"},false,false), /pause retained/);
  assert.match(item._windowStatus({status:"not_configured"},false,false), /no contacts selected/);
});

test("selected gas does not imply the furnace is running", () => {
  const item = card({ hvac_mode: "heat", hvac_action: "idle", selected_source: "gas", running_source: "idle" });
  const view = item.render();
  assert.match(view, /Selected: Gas furnace/);
  assert.match(view, />Idle<\/span>/);
  assert.doesNotMatch(view, />Gas furnace<\/span>/);
});

test("heat-cost recommendation shows a range and never claims automatic switching", () => {
  const item = card({ hvac_mode: "heat", selected_source: "gas", running_source: "gas" });
  item.hass.states["sensor.climado_fuel_cost_recommendation"] = { state: "heat_pump", attributes: {
    status: "ready", preferred_source: "heat_pump", heat_pump_per_kwh: .025,
    furnace_per_kwh_min: .03, furnace_per_kwh_max: .04, outdoor_c: 5, estimated_cop: 3,
    gas_usage_known: false,
  }};
  item.hass.entities["sensor.climado_fuel_cost_recommendation"] = { device_id: "test" };
  const view = item.render();
  assert.match(view, /Advisory only/);
  assert.match(view, /Heat pump estimated cheaper/);
  assert.match(view, /3.00–4.00/);
  assert.match(view, /Selected: Gas furnace/);
  assert.doesNotMatch(view, /Automatic fuel/);
});

test("missing advisory inputs are named without showing cached costs", () => {
  const item = card({});
  const view = item._renderFuelAdvisory({ status: "unavailable", reason: "missing_inputs", missing_inputs: ["cop_points", "electricity_prices"] });
  assert.match(view, /heat-pump performance table, electricity prices/);
  assert.doesNotMatch(view, /estimated cheaper|CAD cents/);
  assert.match(item._renderFuelAdvisory({ status: "unavailable", reason: "outdoor_stale" }), /Outdoor reading is stale/);
});

test("disabled cost advice exposes a collapsed setup checklist without estimates", () => {
  const item = card({});
  const view = item._renderFuelAdvisory({status: "disabled", setup: {
    status: "unavailable", reason: "missing_inputs", missing_inputs: ["cop_points", "electricity_prices"],
  }});
  assert.match(view, /<details class="fuel-advisory fuel-setup">/);
  assert.match(view, /Heating cost setup/);
  assert.match(view, /<li>heat-pump performance table<\/li>/);
  assert.doesNotMatch(view, /estimated cheaper|CAD cents|<details[^>]+open/);
  assert.match(item._renderFuelAdvisory({status: "disabled", setup: {status: "ready"}}), /Inputs valid; cost advice disabled/);
  assert.match(item._renderFuelAdvisory({status: "disabled", setup: {reason: "gas_tariff_expired"}}), /Gas prices outside their valid dates/);
  assert.equal(item._renderFuelAdvisory({status: "disabled"}), "");
});

test("stale rooms keep their labels and replace the old temperature with a status", () => {
  const view = card({main_temp: null, bedroom_temp: 21, room_readings: {
    main: {entity_id: "sensor.main", status: "stale"},
    bedroom: {entity_id: "sensor.bedroom", status: "ready"},
  }}).render();
  assert.match(view, /Main floor/);
  assert.match(view, /class="rval">—<\/span>/);
  assert.match(view, /Stale reading/);
  assert.match(view, /class="rval">21°<\/span>/);
  assert.equal(card({})._roomStatus({status: "not_configured"}), "");
});

function systemCard(overrides = {}) {
  const item = card({ system_control: {
    entry_id: "test", hvac_mode: "off", modes: ["off", "cool", "heat"],
    can_stop: true, can_start: true, source_available: true, ...overrides,
  }});
  item.hass.services = { climado: { set_system_mode: {} } };
  item.calls = [];
  item.hass.callService = async (...args) => item.calls.push(args);
  return item;
}

function restoringCard(overrides = {}) {
  const item = systemCard({hvac_mode:"heat",cloud_hvac_mode:"off",can_start:false,can_stop:false,heat_source:null});
  Object.assign(item.hass.states["sensor.climado_effective_mode"].attributes, {
    windows_restoring:true, windows_pending:true,
    windows_restoration:{mode:"heat",heat_source:"gas",phase:"paused"},
    thermostat_feedback:{source:"local",fresh:true,hvac_mode:"heat",hvac_action:"idle",target:21,mode_disagreement:true},
    ...overrides,
  });
  item.hass.states["sensor.climado_effective_mode"].state = "windows_open";
  item.hass.states["sensor.climado_control_reason"].state = "windows_restoring";
  return item;
}

test("Automatic identifies both selected sources independently of activity and next intent", () => {
  for (const [source, label] of [["gas", "Gas furnace"], ["heat_pump", "Heat pump"]]) {
    const item = systemCard({hvac_mode:"heat",heat_source:source});
    const attrs = item.hass.states["sensor.climado_effective_mode"].attributes;
    Object.assign(attrs, {auto_fuel:{enabled:true,target:source === "gas" ? "heat_pump" : "gas"},hvac_action:"idle"});
    const render = () => item._renderAutomaticSource(item._entities(), attrs.system_control, attrs.auto_fuel, attrs.thermostat_feedback);
    assert.match(render(), new RegExp(`Automatic · ${label}`));
    assert.match(render(), /Selected source · Idle/);
    attrs.thermostat_feedback = {source:"local",fresh:true,hvac_action:"heating"};
    assert.match(render(), /Selected source · Heating/);
    attrs.thermostat_feedback.hvac_action = "fan";
    assert.match(render(), /Selected source · Fan only/);
    attrs.auto_fuel.enabled = false;
    assert.equal(render(), "");
  }
});

test("Automatic source status distinguishes handoff, pause and unconfirmed fuel", () => {
  const item = systemCard({hvac_mode:"heat",heat_source:"gas"});
  const attrs = item.hass.states["sensor.climado_effective_mode"].attributes;
  attrs.auto_fuel = {enabled:true,phase:"stopping",target:"heat_pump"};
  const render = () => item._renderAutomaticSource(item._entities(), attrs.system_control, attrs.auto_fuel, attrs.thermostat_feedback);
  assert.match(render(), /Switching to Heat pump/);
  assert.doesNotMatch(render(), /Selected source/);
  attrs.windows_open = true;
  assert.match(render(), /Automatic paused/);
  assert.match(render(), /Windows open/);
  attrs.windows_open = false;
  attrs.auto_fuel.phase = null;
  for (const changes of [{source_available:false}, {pending:{mode:"heat"}}, {heat_source:null}]) {
    const previous = {...attrs.system_control};
    Object.assign(attrs.system_control, changes);
    assert.match(render(), /Source unconfirmed/);
    attrs.system_control = previous;
  }
  attrs.thermostat_feedback = {mode_disagreement:true};
  assert.match(render(), /Source unconfirmed/);
  attrs.system_control.hvac_mode = "off";
  assert.match(render(), /Automatic paused/);
  assert.match(render(), /System off/);
});

test("restoration shows locally reported target and saved fuel intent without confirming fuel", async () => {
  const item = restoringCard();
  const view = item.render();
  assert.match(view, /class="mode">Restoring heating/);
  assert.match(view, /class="target">21°/);
  assert.match(view, /Target reported locally; Ecobee confirmation pending/);
  assert.match(view, /Restoring Gas furnace · awaiting Ecobee confirmation/);
  assert.doesNotMatch(view, /Selected: Gas furnace/);
  assert.doesNotMatch(view, /class="mode">Windows open/);
  for (const source of ["Heat pump","Gas furnace","Automatic"]) {
    assert.match(view, new RegExp(`aria-label=Heating source ${source}\\s+aria-pressed=false`));
  }
  await item._setSystemMode(item._entities(), "heat");
  assert.equal(item.calls.length, 0);
});

test("local restoration target is hidden for stale, cloud, wrong-mode and invalid observations", () => {
  for (const feedback of [
    {source:"local",fresh:false,hvac_mode:"heat",target:21},
    {source:"cloud",fresh:true,hvac_mode:"heat",target:21},
    {source:"local",fresh:true,hvac_mode:"off",target:21},
    {source:"local",fresh:true,hvac_mode:"cool",target:21},
    {source:"local",fresh:true,hvac_mode:"heat",target:null},
    {source:"local",fresh:true,hvac_mode:"heat",target:Infinity},
  ]) {
    const view = restoringCard({thermostat_feedback:feedback}).render();
    assert.match(view, /class="target">—</);
    assert.doesNotMatch(view, /Target reported locally/);
  }
});

test("restoration never presents local target as a resolved Climado target", () => {
  const item = restoringCard();
  item.hass.states["sensor.climado_resolved_target"].state = "20";
  assert.match(item.render(), /class="target">20°/);
  assert.doesNotMatch(item.render(), /Target reported locally/);
  item.hass.states["sensor.climado_resolved_target"].state = "unknown";
  item.hass.states["sensor.climado_effective_mode"].attributes.windows_restoring = false;
  assert.match(item.render(), /class="target">—</);
});

test("saved fuel is not inferred from HomeKit and pending Off does not display a heating target", () => {
  const missing = restoringCard({windows_restoration:{mode:"heat",phase:"paused"}}).render();
  assert.doesNotMatch(missing, /Restoring Gas furnace|Restoring Heat pump/);
  const pausing = restoringCard({windows_restoration:{mode:"heat",heat_source:"gas",phase:"pausing"}}).render();
  assert.match(pausing, /Finishing pause/);
  assert.match(pausing, /Will restore Gas furnace/);
  assert.match(pausing, /class="target">—</);
});

test("cooling restoration uses local cooling target and no heating-source intent", () => {
  const view = restoringCard({
    windows_restoration:{mode:"cool",heat_source:null,phase:"paused"},
    thermostat_feedback:{source:"local",fresh:true,hvac_mode:"cool",target:23},
  }).render();
  assert.match(view, /Restoring cooling/);
  assert.match(view, /class="target">23°/);
  assert.doesNotMatch(view, /Restoring Gas furnace|Restoring Heat pump/);
});

test("source draft does not start equipment and Heat sends the explicit selected source", async () => {
  const item = systemCard();
  const e = item._entities();
  item._chooseHeatSource(e, "gas");
  assert.equal(item.calls.length, 0);
  assert.match(item.render(), /Next heating source/);
  await item._setSystemMode(e, "heat");
  assert.equal(JSON.stringify(item.calls), JSON.stringify([["climado", "set_system_mode", { entry_id: "test", hvac_mode: "heat", heat_source: "gas" }]]));
  assert.equal(item._systemState(e).hvac_mode, "off"); // No optimistic active mode.
});

test("cool and off commands never send an auxiliary switch toggle", async () => {
  const item = systemCard();
  await item._setSystemMode(item._entities(), "cool");
  assert.equal(item.calls[0][2].heat_source, undefined);
  const active = systemCard({ hvac_mode: "heat", can_start: false, heat_source: "gas" });
  active._chooseHeatSource(active._entities(), "heat_pump");
  await active._setSystemMode(active._entities(), "off");
  assert.equal(active.calls[0][2].hvac_mode, "off");
  assert.equal(active._heatSource, "gas");
  assert.match(active.render(), /Heating source/);
});

test("automatic, unavailable, Windows paused and unsupported modes do not dispatch", async () => {
  const item = systemCard({ can_start: false, can_stop: false });
  const e = item._entities();
  item._chooseHeatSource(e, "auto");
  for (const mode of ["heat", "cool", "off", "heat_cool"]) await item._setSystemMode(e, mode);
  assert.equal(item.calls.length, 0);
  assert.equal(item._heatSource, "heat_pump");
  assert.match(item.render(), /Automatic fuel selection is not available/);
});

test("Automatic is an explicit backend policy toggle, not a direct HVAC command", async () => {
  const item = systemCard({ hvac_mode: "heat", cloud_hvac_mode: "heat", heat_source: "gas", can_start: false });
  item.hass.services.climado.set_fuel_automation = {};
  const attrs = item.hass.states["sensor.climado_effective_mode"].attributes;
  attrs.auto_fuel = { enabled: false };
  await item._toggleFuelAutomation(item._entities());
  assert.equal(JSON.stringify(item.calls), JSON.stringify([["climado", "set_fuel_automation", {entry_id:"test", enabled:true}]]));
  assert.doesNotMatch(item.render(), /Heating source Automatic aria-pressed=true/);
  attrs.auto_fuel = { enabled: true, outdoor_c: 3, reason: "Keeping current heating source" };
  assert.match(item.render(), /Heating source Automatic aria-pressed=true/);
  assert.match(item.render(), /Keeping current heating source/);
  await item._toggleFuelAutomation(item._entities());
  assert.equal(item.calls[1][2].enabled, false);
});

test("Automatic backend failure is visible and does not claim activation", async () => {
  const item = systemCard({ hvac_mode: "heat", heat_source: "gas", can_start: false });
  item.hass.services.climado.set_fuel_automation = {};
  item.hass.callService = async () => { throw new Error("Configure outdoor temperature"); };
  await item._toggleFuelAutomation(item._entities());
  assert.equal(item._systemBusy, false);
  assert.match(item.render(), /Configure outdoor temperature/);
  assert.doesNotMatch(item.render(), /Heating source Automatic aria-pressed=true/);
});

test("pending confirmation blocks new starts but allows an explicit Off", async () => {
  const item = systemCard({ can_start: false, pending: { mode: "heat", source: "gas" } });
  assert.match(item.render(), /Waiting for thermostat confirmation/);
  await item._setSystemMode(item._entities(), "heat");
  assert.equal(item.calls.length, 0);
  await item._setSystemMode(item._entities(), "off");
  assert.equal(item.calls.length, 1);
});

test("Windows pause permits a source draft but never starts heating", async () => {
  const item = systemCard({ cloud_hvac_mode: "off", can_start: false, can_stop: false,
    blocked_reason: "Windows open pause or restoration is active" });
  const e = item._entities();
  assert.equal(item._canChooseHeatSource(item._systemState(e)), true);
  item._chooseHeatSource(e, "gas");
  assert.equal(item._heatSource, "gas");
  assert.match(item.render(), /Heating source Gas furnace aria-pressed=true/);
  await item._setSystemMode(e, "heat");
  assert.equal(item.calls.length, 0);
  // Once the backend permits a start, Heat still needs an explicit click.
  item._systemState(e).can_start = true;
  assert.equal(item.calls.length, 0);
  await item._setSystemMode(e, "heat");
  assert.equal(item.calls.length, 1);
  assert.equal(item.calls[0][2].heat_source, "gas");
});

test("source drafting stays locked for active, conflicting, pending or unavailable systems", () => {
  for (const overrides of [
    { hvac_mode: "heat" }, { hvac_mode: "cool" }, { hvac_mode: null },
    { cloud_hvac_mode: "heat" }, { cloud_hvac_mode: null },
    { pending: { mode: "heat", source: "heat_pump" } },
    { source_available: false }, { modes: ["off", "cool"] },
  ]) {
    const item = systemCard(overrides);
    assert.equal(item._canChooseHeatSource(item._systemState(item._entities())), false);
    item._chooseHeatSource(item._entities(), "gas");
    assert.equal(item._heatSource, "heat_pump");
    assert.equal(item.calls.length, 0);
  }
  const item = systemCard();
  item._systemBusy = true;
  item._chooseHeatSource(item._entities(), "gas");
  assert.equal(item._heatSource, "heat_pump");
});

test("failed mode requests show the error and release the local busy guard", async () => {
  const item = systemCard();
  item.hass.callService = async () => { throw new Error("Thermostat offline"); };
  await item._setSystemMode(item._entities(), "heat");
  assert.equal(item._systemBusy, false);
  assert.match(item.render(), /Thermostat offline/);
});

test("older backends do not show unsupported system controls", () => {
  assert.doesNotMatch(card({}).render(), /aria-label="System controls"/);
  const item = systemCard();
  item.hass.services = {};
  assert.doesNotMatch(item.render(), /aria-label="System controls"/);
});

test("System off is distinct from disabled Climado control and scheduled Sleep", () => {
  const item = card({ hvac_mode: "off" });
  item.hass.states["sensor.climado_effective_mode"].state = "inactive";
  item.hass.states["sensor.climado_control_reason"].state = "thermostat_off";
  assert.match(item.render(), /class="mode">System off/);
  assert.equal(item._humanMode("disabled"), "Control disabled");
});

test("heating alerts are visible without enabling or changing equipment", () => {
  const item = card({ heating_alerts: { issues: [{ code: "windows_cold", message: "Heating remains paused" }] } });
  assert.match(item.render(), /class="heating-alert" role="alert"/);
  assert.match(item.render(), /Heating remains paused/);
});

test("heating targets use entity metadata even after an entity is renamed", async () => {
  const item = card({});
  item.hass.states["number.renamed_target"] = { state: "20", attributes: { climado_key: "heat_home", min: 10, max: 28, step: .5 } };
  item.hass.entities["number.renamed_target"] = { device_id: "test" };
  assert.equal(item._entities().heat_home, "number.renamed_target");
  assert.match(item.render(), /Heating Home target/);
  let call;
  item.hass.callService = async (...args) => { call = args; };
  await item._setHeatingTarget("number.renamed_target", { value: "20.5" });
  assert.equal(JSON.stringify(call), JSON.stringify(["number", "set_value", { entity_id: "number.renamed_target", value: 20.5 }]));
  call = undefined;
  const input = { value: "35" };
  await item._setHeatingTarget("number.renamed_target", input);
  assert.equal(call, undefined);
  assert.equal(input.value, "20");
});

test("local heat confirmation keeps fuel confirmation distinct and shows elapsed time", () => {
  const item = systemCard({ can_start: false, request: {
    mode: "heat", source: "gas", status: "pending", mode_confirmed_locally: true,
    requested_at: new Date(Date.now() - 65000).toISOString(),
  }});
  assert.match(item.render(), /Heat mode confirmed locally; waiting for fuel confirmation \(1m \d+s\)/);
  item._systemState(item._entities()).request = {
    mode: "heat", source: "gas", status: "confirmed", confirmation_source: "local + Ecobee fuel",
  };
  assert.match(item.render(), /Gas furnace heating mode confirmed \(local \+ Ecobee fuel\)/);
  assert.doesNotMatch(item.render(), /Gas furnace running/);
});

test("sending is immediate and does not optimistically change reported mode", async () => {
  const item = systemCard();
  let done;
  item.hass.callService = () => new Promise(resolve => { done = resolve; });
  const request = item._setSystemMode(item._entities(), "heat");
  assert.match(item.render(), /Sending heat pump heating request/);
  assert.equal(item._systemState(item._entities()).hvac_mode, "off");
  done();
  await request;
  assert.equal(item._systemBusy, false);
});

test("local activity and target confirmation do not infer running fuel or native Sleep", () => {
  const item = systemCard();
  Object.assign(item.hass.states["sensor.climado_effective_mode"].attributes, {
    hvac_action: "idle", running_source: "gas", command_pending: true,
    thermostat_feedback: { source: "local", fresh: true, hvac_mode: "heat", hvac_action: "heating", target_confirmed_locally: true,
      reported_at: new Date().toISOString(), local_status: "ready" },
  });
  const view = item.render();
  assert.match(view, /Target confirmed locally; syncing Ecobee/);
  assert.match(view, /Local feedback: last report \d+s ago/);
  assert.match(view, />Heating<\/span>/);
  assert.doesNotMatch(view, /class="rval[^"]*">Gas furnace<\/span>/);
  assert.doesNotMatch(view, /class="rlbl">AC<\/span>/);
});

test("stale local feedback falls back visibly and refresh errors are not command failures", () => {
  const item = systemCard();
  Object.assign(item.hass.states["sensor.climado_effective_mode"].attributes, {
    thermostat_feedback: { source: "cloud", fresh: false, local_status: "unavailable_or_stale",
      reported_at: new Date(Date.now() - 660000).toISOString(), refresh_error: "Cloud unavailable" },
  });
  const view = item.render();
  assert.match(view, /Local feedback unavailable or stale/);
  assert.match(view, /Ecobee cloud feedback: last report 11m/);
  assert.match(view, /Status refresh delayed; no equipment command repeated/);
  assert.doesNotMatch(view, /Thermostat command failed/);
});

test("Off remains available when the cloud disagrees with local Off", async () => {
  const item = systemCard({ hvac_mode: "off", cloud_hvac_mode: "heat", can_start: false });
  await item._setSystemMode(item._entities(), "off");
  assert.equal(item.calls.length, 1);
  assert.equal(item.calls[0][2].hvac_mode, "off");
});

test("cloud Off is not shown as the selected heating source during local mode disagreement", () => {
  const item = card({ hvac_mode: "off", selected_source: "off", thermostat_feedback: {
    source: "local", fresh: true, hvac_mode: "heat", hvac_action: "heating", mode_disagreement: true,
  }});
  assert.match(item.render(), /Heating source: waiting for Ecobee confirmation/);
  assert.doesNotMatch(item.render(), /Selected: Off/);
});
