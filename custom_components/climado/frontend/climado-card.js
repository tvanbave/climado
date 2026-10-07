/**
 * Climado Card (M3 draft)
 * Alarmo-style control surface + TOU/ULO colored rate grid for the Climado
 * integration.
 *
 * No build step: imports Lit from a CDN so it can be dropped into /config/www
 * and registered as a Lovelace resource.
 *
 * Config:
 *   type: custom:climado-card
 *   entity: select.climado_main_floor_mode   # any Climado entity; siblings auto-discovered
 *   # optional:
 *   # climate: climate.main_floor             # to show current room temp
 *   # rate_plan: { weekday: [[0,7,"ultra_low"],...], weekend: [...] }
 *   # rate_editor: true                       # advanced: edit/save both schedules
 *
 * Status: fully functional against the v0.3+ backend. The card ships inside the
 * integration and is served + auto-registered at /climado_static/climado-card.js;
 * rate-grid Save persists via the climado.set_rate_plan service.
 */
import {
  LitElement,
  html,
  css,
} from "https://unpkg.com/lit@3.1.0/index.js?module";

const TIERS = {
  ultra_low: { name: "Ultra-low", color: "#2e7d32" },
  off_peak: { name: "Off-peak", color: "#66bb6a" },
  mid_peak: { name: "Mid-peak", color: "#f9a825" },
  on_peak: { name: "On-peak", color: "#e53935" },
};

const TIER_CYCLE = ["ultra_low", "off_peak", "mid_peak", "on_peak"];
const EMPTY_STATES = new Set(["unknown", "unavailable", "none", ""]);

const DEFAULT_PLAN = {
  weekday: [
    [0, 7, "ultra_low"],
    [7, 16, "mid_peak"],
    [16, 21, "on_peak"],
    [21, 23, "mid_peak"],
    [23, 24, "ultra_low"],
  ],
  weekend: [
    [0, 7, "ultra_low"],
    [7, 23, "off_peak"],
    [23, 24, "ultra_low"],
  ],
};

const MODES = ["auto", "home", "away", "sleep", "vacation"];
const MODE_ICON = {
  auto: "mdi:circle-slice-8",
  home: "mdi:home",
  away: "mdi:weather-night",
  sleep: "mdi:bed",
  vacation: "mdi:bag-suitcase",
};

function hoursEqual(a, b) {
  return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => v === b[i]);
}

function blocksToHours(blocks) {
  // -> array[24] of tier ids
  const hours = new Array(24).fill(TIER_CYCLE[0]);
  for (const [start, end, tier] of blocks) {
    for (let h = start; h < end; h++) hours[h] = tier;
  }
  return hours;
}

function hoursToBlocks(hours) {
  const blocks = [];
  let start = 0;
  for (let h = 1; h <= 24; h++) {
    if (h === 24 || hours[h] !== hours[start]) {
      blocks.push([start, h, hours[start]]);
      start = h;
    }
  }
  return blocks;
}

function cleanValue(value) {
  if (value === undefined || value === null) return null;
  const text = String(value);
  return EMPTY_STATES.has(text.toLowerCase()) ? null : value;
}

function numericValue(value) {
  const clean = cleanValue(value);
  if (clean === null) return null;
  const num = Number(clean);
  return Number.isFinite(num) ? num : null;
}

class ClimadoCard extends LitElement {
  static get properties() {
    return { hass: {}, _config: {}, _draft: { state: true }, _heatSource: { state: true }, _systemBusy: { state: true }, _systemError: { state: true } };
  }

  static getConfigElement() {
    return document.createElement("climado-card-editor");
  }

  static getStubConfig(hass) {
    const sel = Object.keys(hass.states).find(
      (e) => e.startsWith("select.") && e.includes("climado") && e.includes("mode")
    );
    return { entity: sel || "" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Set 'entity' to a Climado entity");
    this._config = config;
    this._draft = null; // lazy-init from plan
    this._baseDraft = null;
    this._heatSource = "heat_pump";
    this._systemBusy = false;
    this._systemError = null;
  }

  getCardSize() {
    return 6;
  }

  connectedCallback() {
    super.connectedCallback();
    this._statusTimer = setInterval(() => {
      if (this.hass && this._config) this.requestUpdate();
    }, 1000);
  }

  disconnectedCallback() {
    clearInterval(this._statusTimer);
    super.disconnectedCallback();
  }

  _elapsed(iso) {
    const at = Date.parse(iso);
    if (!Number.isFinite(at)) return "";
    const seconds = Math.max(0, Math.floor((Date.now() - at) / 1000));
    return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  }

  _systemStatus(system) {
    const request = this._systemBusy ? this._sendingRequest : system.request || system.pending;
    if (!request) return system.blocked_reason;
    const label = request.mode === "heat" ? `${this._fuelLabel(request.source)} heating` : request.mode === "cool" ? "Cooling" : "Off";
    const elapsed = this._elapsed(request.requested_at);
    if (this._systemBusy) return `Sending ${label.toLowerCase()} request`;
    if (request.status === "failed") return `${label} request not confirmed`;
    if (request.status === "confirmed") return `${label} mode confirmed (${request.confirmation_source || "Ecobee"})`;
    if (request.mode_confirmed_locally && request.mode === "heat") return `Heat mode confirmed locally; waiting for fuel confirmation${elapsed ? ` (${elapsed})` : ""}`;
    return `${label} requested${elapsed ? ` (${elapsed})` : ""}. Waiting for thermostat confirmation`;
  }

  _feedbackNote(feedback) {
    if (!feedback) return "";
    const age = this._elapsed(feedback.reported_at);
    const source = feedback.source === "local" ? "Local feedback" : "Ecobee cloud feedback";
    const fallback = feedback.local_status === "unavailable_or_stale" ? "Local feedback unavailable or stale. " : "";
    return `${fallback}${source}${age ? `: last report ${age} ago` : ""}${feedback.fresh ? "" : " (stale or unavailable)"}`;
  }

  // ---- entity discovery ----
  _entities() {
    const reg = this.hass.entities || {};
    const anchor = reg[this._config.entity];
    const deviceId = anchor && anchor.device_id;
    const found = {};
    const ids = deviceId
      ? Object.keys(reg).filter((e) => reg[e].device_id === deviceId)
      : [this._config.entity];
    for (const id of ids) {
      const key = this._attr(id, "climado_key");
      if (id.startsWith("number.") && ["heat_home", "heat_away", "heat_vacation", "heat_prearrival"].includes(key)) found[key] = id;
      else if (id.includes("fuel_cost_recommendation") || id.includes("fuel_recommendation")) found.fuel_recommendation = id;
      else if (id.startsWith("select.")) found.mode = id;
      else if (id.includes("effective_mode")) found.effective_mode = id;
      else if (id.includes("control_reason") || id.includes("_reason")) found.reason = id;
      else if (id.includes("resolved_target")) found.target = id;
      else if (id.includes("rate_tier")) found.tier = id;
      else if (id.includes("presence")) found.presence = id;
      else if (id.includes("climado_control")) found.enable = id;
      else if (id.startsWith("switch.") && id.includes("vacation")) found.vacation = id;
      else if (id.startsWith("switch.") && id.includes("windows_open")) found.windows = id;
      else if (id.startsWith("button.") && id.includes("heading_home")) found.prearrival = id;
      else if (id.startsWith("button.") && id.includes("resume")) found.resume = id;
    }
    // explicit overrides
    return { ...found, climate: this._config.climate };
  }

  _state(id) {
    return id && this.hass.states[id] ? this.hass.states[id] : null;
  }

  _available(id) {
    const state = this._state(id);
    return !!state && cleanValue(state.state) !== null;
  }

  _callable(id) {
    const state = this._state(id);
    return !!state && state.state !== "unavailable";
  }

  _attr(id, key) {
    const s = this._state(id);
    return s && s.attributes ? cleanValue(s.attributes[key]) : undefined;
  }

  _backendPlan(e) {
    const attr = e && this._state(e.tier)?.attributes?.plan;
    const plan = attr || this._config.rate_plan || DEFAULT_PLAN;
    return plan?.weekday && plan?.weekend ? plan : DEFAULT_PLAN;
  }

  // ---- actions ----
  async _setHeatingTarget(entity, input) {
    const value = input.value.trim() === "" ? NaN : Number(input.value);
    const min = numericValue(this._attr(entity, "min")) ?? 10;
    const max = numericValue(this._attr(entity, "max")) ?? 28;
    if (!this._available(entity) || !Number.isFinite(value) || value < min || value > max) {
      input.value = this._state(entity)?.state || "";
      this._systemError = "Heating target is outside the supported range";
      return;
    }
    try {
      await this.hass.callService("number", "set_value", { entity_id: entity, value });
      this._systemError = null;
    } catch (err) {
      input.value = this._state(entity)?.state || "";
      this._systemError = err?.message || "Heating target could not be saved";
    }
  }

  _systemState(e) {
    return this._attr(e.effective_mode, "system_control");
  }

  _canSetSystem(system, mode) {
    if (!system?.entry_id || this._systemBusy || !this.hass.services?.climado?.set_system_mode) return false;
    if (!system.modes?.includes(mode)) return false;
    if (mode === "off") return system.can_stop && (system.hvac_mode !== "off" || (system.cloud_hvac_mode && system.cloud_hvac_mode !== "off") || !!system.pending);
    return system.can_start && (mode !== "heat" || system.source_available);
  }

  _chooseHeatSource(e, source) {
    const system = this._systemState(e);
    if (!system?.can_start || !system.source_available || this._systemBusy || !["heat_pump", "gas"].includes(source)) return;
    this._heatSource = source;
    this._systemError = null;
  }

  async _setSystemMode(e, mode) {
    const system = this._systemState(e);
    if (!this._canSetSystem(system, mode)) return;
    this._systemBusy = true;
    this._sendingRequest = { mode, source: mode === "heat" ? this._heatSource : null, requested_at: new Date().toISOString() };
    this._systemError = null;
    if (mode === "off" && ["heat_pump", "gas"].includes(system.heat_source)) this._heatSource = system.heat_source;
    const data = { entry_id: system.entry_id, hvac_mode: mode };
    if (mode === "heat") data.heat_source = this._heatSource;
    try {
      await this.hass.callService("climado", "set_system_mode", data);
    } catch (err) {
      this._systemError = err?.message || "System command failed";
    } finally {
      this._systemBusy = false;
    }
  }

  _setMode(e, mode) {
    if (!this._available(e.mode)) return;
    this.hass.callService("select", "select_option", {
      entity_id: e.mode,
      option: mode,
    });
  }

  _toggle(entity) {
    if (!this._available(entity)) return;
    this.hass.callService("switch", "toggle", { entity_id: entity });
  }

  _press(entity) {
    if (!this._callable(entity)) return;
    this.hass.callService("button", "press", { entity_id: entity });
  }

  _notify(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: { message },
        bubbles: true,
        composed: true,
      })
    );
  }

  _paint(profile, hour) {
    if (!this._config?.rate_editor) return;
    if (!this._draft) this._initDraft(this._entities());
    const cur = this._draft[profile][hour];
    const next = TIER_CYCLE[(TIER_CYCLE.indexOf(cur) + 1) % TIER_CYCLE.length];
    this._draft = {
      ...this._draft,
      [profile]: this._draft[profile].map((t, i) => (i === hour ? next : t)),
    };
  }

  _initDraft(e) {
    const plan = this._backendPlan(e);
    this._draft = {
      weekday: blocksToHours(plan.weekday),
      weekend: blocksToHours(plan.weekend),
    };
    this._baseDraft = {
      weekday: [...this._draft.weekday],
      weekend: [...this._draft.weekend],
    };
  }

  async _save() {
    if (!this._draft || !this._dirty()) return;
    const plan = {
      weekday: hoursToBlocks(this._draft.weekday),
      weekend: hoursToBlocks(this._draft.weekend),
    };
    try {
      await this.hass.callService("climado", "set_rate_plan", { plan });
      this._baseDraft = {
        weekday: [...this._draft.weekday],
        weekend: [...this._draft.weekend],
      };
      this.requestUpdate();
      this._notify("Climado: rate plan saved");
    } catch (err) {
      this._notify(
        `Climado: failed to save rate plan${err?.message ? ` — ${err.message}` : ""} (see Home Assistant logs).`
      );
    }
  }

  _dirty() {
    return !!(
      this._draft &&
      this._baseDraft &&
      (!hoursEqual(this._draft.weekday, this._baseDraft.weekday) ||
        !hoursEqual(this._draft.weekend, this._baseDraft.weekend))
    );
  }

  // ---- render ----
  render() {
    if (!this.hass || !this._config) return html``;
    const e = this._entities();
    if (!e.mode) {
      return html`<ha-card
        ><div class="pad">Climado entity not found: ${this._config.entity}</div></ha-card
      >`;
    }
    if (!this._draft) this._initDraft(e);

    const a = (k) => this._attr(e.effective_mode, k);
    const modeReady = this._available(e.mode);
    const effectiveReady = this._available(e.effective_mode);
    const controlsReady = modeReady && effectiveReady;
    const editor = this._config.rate_editor === true;
    const mode = cleanValue(this._state(e.mode)?.state) || "auto";
    const eff = cleanValue(this._state(e.effective_mode)?.state);
    const reason = cleanValue(this._state(e.reason)?.state);
    const target = numericValue(this._state(e.target)?.state);
    const tier = cleanValue(this._state(e.tier)?.state);
    const tierId = this._tierId(e, tier);
    const rateProfile = this._rateProfile(e);
    const presence = cleanValue(this._state(e.presence)?.state);
    const mainT = numericValue(a("main_temp"));
    const bedT = numericValue(a("bedroom_temp"));
    const feedback = a("thermostat_feedback");
    const localFeedback = feedback?.source === "local" && feedback.fresh;
    const hvac = cleanValue(localFeedback ? feedback.hvac_action : a("hvac_action"));
    const cooling = hvac === "cooling";
    const heating = (localFeedback ? feedback.hvac_mode : a("hvac_mode")) === "heat";
    const regulating = cleanValue(a("regulating"));
    const enableReady = this._available(e.enable);
    const vacationReady = this._available(e.vacation);
    const prearrivalReady = this._callable(e.prearrival) && !["inactive", "disabled", "windows_open"].includes(eff);
    const windowsReady = this._available(e.windows);
    const windowsOn = windowsReady && this._state(e.windows)?.state === "on";
    const resumeReady = this._callable(e.resume);
    const enableOn = enableReady && this._state(e.enable)?.state === "on";
    const vacOn = vacationReady && this._state(e.vacation)?.state === "on";
    const preUntil = cleanValue(a("prearrival_until"));
    const holdActive = a("manual_hold_active");
    const holdUntil = holdActive ? a("manual_hold_until") : null;
    const holdVal = a("manual_hold_value");
    const holdTemp = Array.isArray(holdVal) && holdVal[0] === "temp" ? numericValue(holdVal[1]) : null;
    const nextT = a("next_transition");
    const overrideUntil = cleanValue(a("manual_override_until"));
    const unavailable = !effectiveReady;
    const off = eff === "disabled" || (enableReady && !enableOn);
    const dirty = this._dirty();
    const canResume = !!(holdUntil || preUntil);
    const bigTemp = holdTemp != null ? holdTemp : target;
    const bigLabel = holdTemp != null ? "Hold" : regulating === "bedroom" ? "Bedroom target" : "Target";
    const title = unavailable ? "Unavailable" : reason === "thermostat_off" ? "System off" : reason === "system_mode_pending" ? "Waiting" : this._humanMode(eff);
    const subtitle = unavailable
      ? "Waiting for Climado to report its state"
      : this._humanReason(reason, heating);
    const timedOverride = overrideUntil && (mode === "home" || mode === "sleep");
    const controlLabel = mode === "auto"
      ? "Auto control"
      : `Override: ${this._humanMode(mode)}${timedOverride ? ` until ${this._fmt(overrideUntil)}` : ""}`;
    const hvacInfo = this._hvacInfo(hvac);
    const running = a("running_source");
    const selected = a("selected_source");
    // HomeKit heating/idle is not evidence of which fuel is running.
    const equipmentLabel = localFeedback ? hvacInfo.label : running && running !== "unknown" ? this._fuelLabel(running) : hvacInfo.label;
    const advisory = this._state(e.fuel_recommendation)?.attributes;
    const commandPending = a("command_pending") === true || a("windows_pending") === true;
    const commandError = a("command_error") || a("windows_error");

    return html`
      <ha-card class="${off ? "off" : ""} ${unavailable ? "unavailable" : ""}">
        <div class="head">
          <div>
            <div class="mode">${title}</div>
            <div class="reason">
              <span class="dot ${cooling ? "cool" : ""}"></span>${subtitle}
            </div>
            <div class="control-note">${controlLabel}</div>
            ${commandError
              ? html`<div class="control-note">Thermostat command failed; retrying</div>`
              : commandPending
                ? html`<div class="control-note">${feedback?.target_confirmed_locally ? "Target confirmed locally; syncing Ecobee" : "Waiting for thermostat confirmation"}</div>`
                : ""}
          </div>
          <div class="temps">
            <div class="target">${this._temp(bigTemp)}</div>
            <div class="tlabel">${bigLabel}</div>
          </div>
        </div>

        ${(a("heating_alerts")?.issues || []).map(issue => html`<div class="heating-alert" role="alert"><ha-icon icon="mdi:alert-outline"></ha-icon><span>${issue.message}</span></div>`)}
        ${a("heating_alerts")?.monitoring === "unavailable" ? html`<div class="control-note">Heating monitoring unavailable: indoor readings are missing or stale</div>` : ""}

        <div class="rooms">
          ${mainT != null
            ? html`<div class="room ${regulating === "main" ? "reg" : ""}">
                <span class="rval">${this._temp(mainT)}</span><span class="rlbl">Main floor</span>
              </div>`
            : ""}
          ${bedT != null
            ? html`<div class="room ${regulating === "bedroom" ? "reg" : ""}">
                <span class="rval">${this._temp(bedT)}</span><span class="rlbl">Bedroom</span>
              </div>`
            : ""}
          <div class="room">
            <span class="rval ${hvacInfo.active ? "cooling" : ""}">${equipmentLabel}</span>
            <span class="rlbl">${heating ? "Manual fuel" : a("hvac_mode") && a("hvac_mode") !== "cool" ? "HVAC" : "AC"}</span>
          </div>
        </div>

        ${heating && feedback?.mode_disagreement
          ? html`<div class="control-note">Heating source: waiting for Ecobee confirmation</div>`
          : heating && selected
          ? html`<div class="control-note">Selected: ${this._fuelLabel(selected)} · Manual fuel selection</div>`
          : ""}
        ${this._renderFuelAdvisory(advisory)}

        ${this._renderSystemControls(e)}
        ${this._renderHeatingTargets(e)}

        ${nextT && nextT.at
          ? html`<div class="next">${this._nextText(nextT)}</div>`
          : ""}

        <div class="chips">
          ${tier || tierId
            ? html`<span class="chip" style="--c:${TIERS[tierId]?.color || "#999"}">
                ${TIERS[tierId]?.name || tier}
              </span>`
            : html`<span class="chip muted">Rate tier unavailable</span>`}
          ${presence
            ? html`<span class="chip ${presence === "occupied" ? "ok" : "warn"}">${presence}</span>`
            : html`<span class="chip muted">Presence unavailable</span>`}
          ${preUntil
            ? html`<span class="chip pre">${heating ? "preheat" : "pre-cool"} → ${this._fmt(preUntil)}</span>`
            : ""}
          ${holdUntil
            ? html`<span class="chip hold">hold → ${this._fmt(holdUntil)}</span>`
            : ""}
        </div>

        <div class="modes">
          ${MODES.map(
            (m) => html`<button
              class="modebtn ${mode === m ? "sel" : ""}"
              ?disabled=${!modeReady}
              @click=${() => this._setMode(e, m)}
            >
              <ha-icon .icon=${MODE_ICON[m]}></ha-icon><span>${m}</span>
            </button>`
          )}
        </div>

        <div class="row">
          ${e.windows ? html`<label class="tgl">
            <ha-switch
              aria-label="Windows open"
              .checked=${windowsOn}
              .disabled=${!windowsReady}
              @change=${() => this._toggle(e.windows)}
            ></ha-switch>
            Windows open
          </label>` : ""}
          <label class="tgl">
            <ha-switch
              .checked=${enableOn}
              .disabled=${!enableReady}
              @change=${() => this._toggle(e.enable)}
            ></ha-switch>
            Climado control
          </label>
          <label class="tgl">
            <ha-switch
              .checked=${vacOn}
              .disabled=${!vacationReady}
              @change=${() => this._toggle(e.vacation)}
            ></ha-switch>
            Vacation
          </label>
        </div>

        <div class="row">
          <button class="action" ?disabled=${!prearrivalReady} @click=${() => this._press(e.prearrival)}>
            <ha-icon icon="mdi:home-clock"></ha-icon><span>Heading home</span>
          </button>
          <button class="action ghost" ?disabled=${!resumeReady || !canResume} @click=${() => this._press(e.resume)}>
            <ha-icon icon="mdi:restart"></ha-icon><span>Resume</span>
          </button>
        </div>

        <div class="grid-title">
          <span class="grid-main">Rate plan</span>
          <span class="hint">${editor ? "tap an hour to change tier" : this._profileLabel(rateProfile)}</span>
          ${dirty && editor ? html`<span class="unsaved">Unsaved changes</span>` : ""}
        </div>
        ${editor
          ? html`${this._grid("weekday", "Weekday", true)} ${this._grid("weekend", "Weekend / holiday", true)}`
          : this._grid(rateProfile, this._profileLabel(rateProfile), false)}

        <div class="legend">
          ${Object.entries(TIERS).map(
            ([id, t]) =>
              html`<span class="lg"><i style="background:${t.color}"></i>${t.name}</span>`
          )}
        </div>

        ${editor
          ? html`<div class="row">
              <button class="action" ?disabled=${!controlsReady || !dirty} @click=${() => this._save()}>
                <ha-icon icon="mdi:content-save"></ha-icon><span>Save rate plan</span>
              </button>
              <button class="action ghost" ?disabled=${!dirty} @click=${() => this._initDraft(e)}>
                <ha-icon icon="mdi:restore"></ha-icon><span>Reset</span>
              </button>
            </div>`
          : ""}
      </ha-card>
    `;
  }

  _tierId(e, name) {
    // Prefer the backend's canonical tier_id (v0.3.5+); fall back to matching
    // the display-name prefix ("Ultra-low overnight" -> "Ultra-low").
    const id = this._state(e.tier)?.attributes?.tier_id;
    if (id && TIERS[id]) return id;
    const hit = Object.entries(TIERS).find(([, t]) => name && name.startsWith(t.name));
    return hit ? hit[0] : name;
  }

  _rateProfile(e) {
    const profile = this._state(e.tier)?.attributes?.profile;
    return profile === "weekend" ? "weekend" : "weekday";
  }

  _profileLabel(profile) {
    return profile === "weekend" ? "Weekend / holiday" : "Weekday";
  }

  _round(v) {
    const n = Number(v);
    return Number.isFinite(n) ? Math.round(n * 10) / 10 : v;
  }

  _temp(v) {
    const n = numericValue(v);
    return n === null ? "—" : `${this._round(n)}°`;
  }

  _humanMode(m) {
    return (
      {
        pre_arrival: "Heading home",
        manual_hold: "Manual hold",
        sleep: "Sleep",
        away: "Away",
        home: "Home",
        vacation: "Vacation",
        disabled: "Control disabled",
        unavailable: "Unavailable",
        inactive: "Inactive",
        windows_open: "Windows open",
      }[m] || (m || "").replace(/_/g, " ")
    );
  }

  _renderSystemControls(e) {
    const system = this._systemState(e);
    if (!system?.entry_id || !this.hass.services?.climado?.set_system_mode) return html``;
    const heating = system.hvac_mode === "heat";
    const source = heating ? system.heat_source : this._heatSource;
    const sourceReady = system.can_start && system.source_available && system.modes?.includes("heat") && !this._systemBusy;
    const error = this._systemError || system.error;
    const status = this._systemStatus(system);
    const feedback = this._attr(e.effective_mode, "thermostat_feedback");
    return html`<section class="system-controls" aria-label="System controls">
      <div class="system-label">System mode</div>
      <div class="segments" role="group" aria-label="System mode">
        ${[["off", "Off", "mdi:power"], ["cool", "Cool", "mdi:snowflake"], ["heat", "Heat", "mdi:fire"]].map(([value, label, icon]) => html`
          <button class="segment ${system.hvac_mode === value ? "selected" : ""}"
            aria-label=${`System ${label}`} aria-pressed=${system.hvac_mode === value}
            ?disabled=${!this._canSetSystem(system, value)}
            title=${value === "heat" ? `Start heating with ${this._fuelLabel(this._heatSource)}` : label}
            @click=${() => this._setSystemMode(e, value)}>
            <ha-icon .icon=${icon}></ha-icon><span>${label}</span>
          </button>`)}
      </div>
      <div class="system-label">${heating ? "Heating source" : "Next heating source"}</div>
      <div class="segments" role="group" aria-label="Heating source">
        ${[["heat_pump", "Heat pump", "mdi:heat-pump"], ["gas", "Gas furnace", "mdi:fire"], ["auto", "Automatic", "mdi:auto-mode"]].map(([value, label, icon]) => html`
          <button class="segment ${source === value ? "selected" : ""}"
            aria-label=${`Heating source ${label}`} aria-pressed=${source === value}
            ?disabled=${!sourceReady || value === "auto"}
            title=${value === "auto" ? "Automatic fuel selection is not available" : label}
            @click=${() => this._chooseHeatSource(e, value)}>
            <ha-icon .icon=${icon}></ha-icon><span>${label}</span>
          </button>`)}
      </div>
      ${!system.source_available ? html`<div class="control-note">Heating source unavailable</div>` : ""}
      ${status ? html`<div class="control-note" role="status">${status}</div>` : ""}
      ${feedback ? html`<div class="feedback-note">${this._feedbackNote(feedback)}</div>` : ""}
      ${feedback?.mode_disagreement ? html`<div class="control-note">Local and cloud modes differ; comfort commands paused</div>` : ""}
      ${feedback?.refresh_error ? html`<div class="control-note">Status refresh delayed; no equipment command repeated</div>` : ""}
      ${error ? html`<div class="system-error" role="alert">${error}</div>` : ""}
    </section>`;
  }

  _renderHeatingTargets(e) {
    const targets = [["heat_home", "Home"], ["heat_away", "Away"], ["heat_vacation", "Vacation"], ["heat_prearrival", "Pre-arrival"]].filter(([key]) => e[key]);
    if (!targets.length) return html``;
    return html`<details class="heating-targets"><summary>Heating targets</summary>
      <div class="target-inputs">${targets.map(([key, label]) => html`<label>${label} (°C)
        <input type="number" aria-label=${`Heating ${label} target`} .value=${this._available(e[key]) ? this._state(e[key]).state : ""}
          min=${this._attr(e[key], "min") ?? 10} max=${this._attr(e[key], "max") ?? 28} step=${this._attr(e[key], "step") ?? .5}
          ?disabled=${!this._available(e[key])} @change=${event => this._setHeatingTarget(e[key], event.target)}>
      </label>`)}</div><div class="control-note">Sleep: Ecobee comfort setting</div>
      ${this._systemError ? html`<div class="system-error" role="alert">${this._systemError}</div>` : ""}
    </details>`;
  }

  _fuelLabel(source) {
    return ({ heat_pump: "Heat pump", gas: "Gas furnace", mixed: "Multiple sources",
      cooling: "Cooling", fan: "Fan only", idle: "Idle", off: "Off", unknown: "Unknown" })[source] || "Unknown";
  }

  _renderFuelAdvisory(data) {
    if (!data || data.status === "disabled") return "";
    const labels = {
      outdoor_temp_sensor: "outdoor sensor", prices_consistent: "matching price basis",
      electricity_prices: "electricity prices", electricity_valid_from: "electricity start date",
      electricity_valid_until: "electricity end date", electricity_source: "electricity price source",
      gas_blocks: "gas prices", gas_valid_from: "gas start date", gas_valid_until: "gas end date",
      gas_source: "gas price source", gas_kwh_per_m3: "gas energy conversion",
      furnace_efficiency: "furnace efficiency", furnace_aux_kwh_per_heat_kwh: "blower electricity",
      cop_points: "heat-pump performance table", cop_source: "performance data source",
    };
    const reasons = {
      outdoor_unavailable: "Outdoor sensor unavailable", outdoor_stale: "Outdoor reading is stale",
      outdoor_unit: "Outdoor sensor must report Celsius", electricity_tariff_expired: "Electricity prices outside their valid dates",
      gas_tariff_expired: "Gas prices outside their valid dates", outside_cop_range: "Outside the performance table temperature range",
      invalid_inputs: "Cost inputs need attention",
    };
    if (data.status !== "ready") {
      const message = data.reason === "missing_inputs"
        ? `Needed: ${(data.missing_inputs || []).map(key => labels[key] || key).join(", ")}`
        : reasons[data.reason] || "Cost estimate unavailable";
      return html`<section class="fuel-advisory"><div class="fuel-heading">Heating cost estimate</div><div class="control-note">${message}</div></section>`;
    }
    const cost = value => numericValue(value) == null ? "—" : (Number(value) * 100).toFixed(2);
    const gas = data.furnace_per_kwh_min === data.furnace_per_kwh_max
      ? cost(data.furnace_per_kwh_min)
      : `${cost(data.furnace_per_kwh_min)}–${cost(data.furnace_per_kwh_max)}`;
    return html`<section class="fuel-advisory">
      <div class="fuel-heading">Heating cost estimate <span>Advisory only</span></div>
      <div class="fuel-costs"><div><span>Heat pump</span><strong>${cost(data.heat_pump_per_kwh)}</strong></div><div><span>Gas furnace</span><strong>${gas}</strong></div></div>
      <div class="control-note">CAD cents/kWh of delivered heat · Outside ${this._temp(data.outdoor_c)} · COP ${Number(data.estimated_cop).toFixed(2)}</div>
      <div class="fuel-result">${data.preferred_source ? `${this._fuelLabel(data.preferred_source)} estimated cheaper` : "Costs too close to favour a source"}</div>
      ${data.gas_usage_known === false ? html`<div class="control-note">Gas price range: billing-period usage unavailable</div>` : ""}
    </section>`;
  }

  _humanReason(reason, heating = false) {
    if (!reason) return "Waiting for Climado to report its state";
    const map = {
      vacation: "Vacation setback",
      manual_away: "Away (manual)",
      away: "Away — nobody home",
      pre_arrival: "Pre-cooling for your arrival",
      pre_arrival_heat: "Preheating for your arrival",
      heating_not_enabled: "Heating targets are not enabled",
      system_mode_pending: "Waiting for thermostat mode confirmation",
      feedback_mode_mismatch: "Waiting for local and cloud modes to agree",
      thermostat_off: "Thermostat is off",
      windows_open: "Heating and cooling paused; fan unchanged",
      windows_restoring: "Restoring thermostat mode",
      thermostat_mode_changed: "Thermostat mode changed",
      unsupported_hvac_mode: "Thermostat mode unavailable or unsupported",
      unsupported_temperature_unit: "Heating requires Celsius thermostat units",
      manual_hold: "Respecting your manual change",
      manual_sleep: "Sleep (manual)",
      "night/ecobee-sleep": heating ? "Overnight bedroom comfort" : "Overnight — cooling the bedroom",
      "night/fallback": "Overnight",
      disabled: "Climado is off",
    };
    if (map[reason]) return map[reason];
    if (reason.startsWith("home/")) {
      const r = reason.slice(5);
      if (r.startsWith("coast:")) return "Coasting through on-peak";
      if (r.startsWith("precool:")) return "Pre-cooling before on-peak";
      if (r.startsWith("tier:")) return "Home comfort";
    }
    return reason;
  }

  _hvacInfo(hvac) {
    const value = cleanValue(hvac);
    if (value === "cooling") return { label: "Cooling", active: true };
    if (value === "fan") return { label: "Fan only", active: false };
    if (value === "idle") return { label: "Idle", active: false };
    if (value === "heating") return { label: "Heating", active: true };
    return { label: value ? String(value).replace(/_/g, " ") : "Unknown", active: false };
  }

  _nextText(next) {
    if (!next || !next.at) return "";
    const when = new Date(next.at);
    if (Number.isNaN(when.getTime())) return `Next: ${next.label}`;
    const diffMs = when.getTime() - Date.now();
    const minutes = Math.max(0, Math.round(diffMs / 60000));
    const time = this._fmt(next.at);
    const relative =
      minutes < 1
        ? "now"
        : minutes < 90
          ? `in ${minutes} min`
          : `in ${Math.round(minutes / 60)} hr`;
    return `${next.label} ${relative} · ${time}`;
  }

  _fmt(iso) {
    try {
      return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return iso;
    }
  }

  _grid(profile, label, editable = false) {
    const hours = this._draft[profile];
    const nowHour = new Date().getHours();
    return html`
      <div class="gwrap ${editable ? "editable" : ""}">
        <div class="glabel">${label}</div>
        <div class="bar">
          ${hours.map(
            (tier, h) => html`<div
              class="cell ${h === nowHour ? "now" : ""}"
              style="background:${TIERS[tier]?.color}"
              title="${h}:00 — ${TIERS[tier]?.name}"
              @click=${editable ? () => this._paint(profile, h) : undefined}
            ></div>`
          )}
        </div>
      </div>
    `;
  }

  static get styles() {
    return css`
      .heating-alert { display: flex; align-items: flex-start; gap: 8px; padding: 10px 0; border-top: 2px solid var(--warning-color, #b57600); font-size: 13px; overflow-wrap: anywhere; }
      .heating-alert ha-icon { flex: 0 0 24px; color: var(--warning-color, #b57600); }
      .heating-targets summary { cursor: pointer; font-size: 14px; font-weight: 600; padding: 6px 0; }
      .target-inputs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 10px 0; }
      .target-inputs label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; min-width: 0; }
      .target-inputs input { box-sizing: border-box; width: 100%; min-width: 0; height: 40px; font: inherit; font-size: 16px; background: var(--card-background-color, white); color: var(--primary-text-color); border: 1px solid var(--divider-color); border-radius: 4px; padding: 6px; }
      .system-controls { padding: 12px 0; border-top: 1px solid var(--divider-color); border-bottom: 1px solid var(--divider-color); }
      .system-label { font-size: 13px; font-weight: 600; margin: 0 0 6px; }
      .segments { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 12px; border: 1px solid var(--divider-color); border-radius: 6px; overflow: hidden; }
      .segment { min-width: 0; min-height: 64px; border: 0; border-right: 1px solid var(--divider-color); border-radius: 0; padding: 8px 4px; background: var(--card-background-color, white); color: var(--primary-text-color); font: inherit; font-size: 13px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; cursor: pointer; }
      .segment:last-child { border-right: 0; }
      .segment ha-icon { --mdc-icon-size: 20px; width: 20px; height: 20px; }
      .segment span { overflow-wrap: anywhere; }
      .segment:disabled { cursor: default; color: var(--secondary-text-color); opacity: .65; }
      .segment.selected, .segment.selected:disabled { background: var(--primary-color); color: var(--text-primary-color, white); opacity: 1; }
      .segment:focus-visible { outline: 2px solid var(--primary-text-color); outline-offset: -3px; }
      .system-error { font-size: 13px; color: var(--error-color, #b71c1c); overflow-wrap: anywhere; margin-top: 6px; }
      .feedback-note { font-size: 12px; color: var(--secondary-text-color); overflow-wrap: anywhere; margin-top: 6px; }
      .fuel-advisory { border-top: 1px solid var(--divider-color); border-bottom: 1px solid var(--divider-color); padding: 12px 0; margin: 12px 0; overflow-wrap: anywhere; }
      .fuel-heading { font-size: 14px; font-weight: 600; display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; }
      .fuel-heading span { font-weight: 400; color: var(--secondary-text-color); }
      .fuel-costs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 10px 0; }
      .fuel-costs > div { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
      .fuel-costs span { color: var(--secondary-text-color); font-size: 13px; }
      .fuel-costs strong { font-size: 18px; }
      .fuel-result { margin-top: 8px; font-size: 14px; }
      ha-card {
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .pad {
        padding: 16px;
      }
      .head {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 14px;
      }
      .mode {
        font-size: 1.45em;
        font-weight: 600;
        text-transform: capitalize;
        line-height: 1.15;
      }
      .reason {
        color: var(--secondary-text-color);
        font-size: 0.85em;
      }
      .control-note {
        color: var(--secondary-text-color);
        font-size: 0.76em;
        margin-top: 4px;
      }
      ha-card.off {
        opacity: 0.6;
      }
      ha-card.unavailable {
        opacity: 0.78;
      }
      .temps {
        text-align: right;
        min-width: 76px;
      }
      .target {
        font-size: 1.9em;
        font-weight: 600;
        line-height: 1;
      }
      .tlabel {
        color: var(--secondary-text-color);
        font-size: 0.75em;
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
      .reason {
        display: flex;
        align-items: center;
        gap: 5px;
      }
      .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--disabled-text-color, #999);
        display: inline-block;
        flex: none;
      }
      .dot.cool {
        background: #039be5;
        box-shadow: 0 0 0 3px rgba(3, 155, 229, 0.25);
      }
      .rooms {
        display: flex;
        gap: 8px;
      }
      .room {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 1px;
        padding: 8px 4px;
        border-radius: 10px;
        background: var(--secondary-background-color);
      }
      .room.reg {
        outline: 2px solid var(--primary-color);
      }
      .room .rval {
        font-size: 1.15em;
        font-weight: 600;
        line-height: 1.2;
      }
      .room .rval.cooling {
        color: #039be5;
      }
      .room .rlbl {
        font-size: 0.72em;
        color: var(--secondary-text-color);
      }
      .next {
        font-size: 0.82em;
        color: var(--secondary-text-color);
      }
      .chips {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
      }
      .chip {
        border-radius: 12px;
        padding: 2px 10px;
        font-size: 0.8em;
        color: #fff;
        background: var(--c, #777);
      }
      .chip.ok {
        background: #2e7d32;
      }
      .chip.warn {
        background: #8d6e63;
      }
      .chip.muted {
        background: var(--disabled-text-color, #999);
      }
      .chip.pre {
        background: #1565c0;
      }
      .chip.hold {
        background: #6a1b9a;
      }
      .modes {
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 6px;
      }
      .modebtn {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 4px;
        min-height: 58px;
        padding: 8px 4px;
        border: 1px solid var(--divider-color);
        border-radius: 10px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        cursor: pointer;
        text-transform: capitalize;
        font-size: 0.8em;
      }
      .modebtn ha-icon {
        --mdc-icon-size: 20px;
        color: var(--secondary-text-color);
      }
      .modebtn.sel {
        border-color: var(--primary-color);
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
      }
      .modebtn.sel ha-icon {
        color: currentColor;
      }
      .modebtn:disabled,
      .action:disabled {
        opacity: 0.45;
        cursor: not-allowed;
      }
      .row {
        display: flex;
        gap: 12px;
        align-items: center;
        flex-wrap: wrap;
      }
      .tgl {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 0.9em;
      }
      .action {
        flex: 1;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        min-height: 44px;
        padding: 10px;
        border: none;
        border-radius: 10px;
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
        cursor: pointer;
        font-size: 0.9em;
      }
      .action ha-icon {
        --mdc-icon-size: 18px;
      }
      .action.ghost {
        background: var(--secondary-background-color);
        color: var(--primary-text-color);
      }
      .grid-title {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        font-weight: 600;
        margin-top: 4px;
      }
      .grid-title .hint {
        font-weight: 400;
        color: var(--secondary-text-color);
        font-size: 0.8em;
      }
      .grid-title .unsaved {
        border-radius: 12px;
        padding: 2px 8px;
        background: rgba(249, 168, 37, 0.16);
        color: #c17900;
        font-size: 0.72em;
        font-weight: 600;
      }
      .gwrap {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .glabel {
        width: 110px;
        font-size: 0.8em;
        color: var(--secondary-text-color);
      }
      .bar {
        display: grid;
        grid-template-columns: repeat(24, 1fr);
        flex: 1;
        height: 26px;
        min-height: 26px;
        border-radius: 6px;
        overflow: hidden;
      }
      .cell {
        border-right: 1px solid rgba(0, 0, 0, 0.12);
      }
      .gwrap.editable .cell {
        cursor: pointer;
      }
      .cell.now {
        box-shadow:
          inset 0 0 0 2px rgba(255, 255, 255, 0.85),
          inset 0 0 0 4px rgba(0, 0, 0, 0.45);
      }
      .legend {
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        font-size: 0.8em;
        color: var(--secondary-text-color);
      }
      .lg {
        display: flex;
        align-items: center;
        gap: 4px;
      }
      .lg i {
        width: 12px;
        height: 12px;
        border-radius: 3px;
        display: inline-block;
      }
      @media (max-width: 520px) {
        ha-card {
          padding: 14px;
        }
        .head {
          align-items: stretch;
        }
        .modes {
          gap: 5px;
        }
        .modebtn {
          min-height: 54px;
          font-size: 0.72em;
        }
        .gwrap {
          align-items: stretch;
          flex-direction: column;
          gap: 5px;
        }
        .glabel {
          width: auto;
        }
        .row {
          gap: 8px;
        }
        .action {
          min-width: 135px;
        }
      }
    `;
  }
}

class ClimadoCardEditor extends LitElement {
  static get properties() {
    return { hass: {}, _config: {} };
  }

  setConfig(config) {
    this._config = config;
  }

  _schema() {
    return [
      { name: "entity", selector: { entity: { domain: "select" } } },
      { name: "climate", selector: { entity: { domain: "climate" } } },
      { name: "rate_editor", selector: { boolean: {} } },
    ];
  }

  _valueChanged(ev) {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: ev.detail.value },
        bubbles: true,
        composed: true,
      })
    );
  }

  render() {
    if (!this.hass || !this._config) return html``;
    return html`<ha-form
      .hass=${this.hass}
      .data=${this._config}
      .schema=${this._schema()}
      .computeLabel=${(s) =>
        ({
          entity: "Climado mode entity",
          climate: "Thermostat (optional)",
          rate_editor: "Enable rate-plan editor",
        }[s.name] || s.name)}
      @value-changed=${this._valueChanged}
    ></ha-form>`;
  }
}

customElements.define("climado-card", ClimadoCard);
customElements.define("climado-card-editor", ClimadoCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "climado-card",
  name: "Climado Card",
  description: "Presence + TOU/ULO rate control for Climado zones.",
  preview: true,
  documentation: "https://github.com/tvanbave/climado",
});

console.info("%c CLIMADO-CARD %c 0.4.0b2 ", "background:#1565c0;color:#fff", "");
