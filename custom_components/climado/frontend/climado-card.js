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
    return { hass: {}, _config: {}, _draft: { state: true } };
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
  }

  getCardSize() {
    return 6;
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
      if (id.startsWith("select.")) found.mode = id;
      else if (id.includes("effective_mode")) found.effective_mode = id;
      else if (id.includes("control_reason") || id.includes("_reason")) found.reason = id;
      else if (id.includes("resolved_target")) found.target = id;
      else if (id.includes("rate_tier")) found.tier = id;
      else if (id.includes("presence")) found.presence = id;
      else if (id.includes("climado_control")) found.enable = id;
      else if (id.startsWith("switch.") && id.includes("vacation")) found.vacation = id;
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
  }

  async _save() {
    if (!this._draft) return;
    const plan = {
      weekday: hoursToBlocks(this._draft.weekday),
      weekend: hoursToBlocks(this._draft.weekend),
    };
    try {
      await this.hass.callService("climado", "set_rate_plan", { plan });
      this._notify("Climado: rate plan saved");
    } catch (err) {
      this._notify(
        `Climado: failed to save rate plan${err?.message ? ` — ${err.message}` : ""} (see Home Assistant logs).`
      );
    }
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
    const mode = cleanValue(this._state(e.mode)?.state) || "auto";
    const eff = cleanValue(this._state(e.effective_mode)?.state);
    const reason = cleanValue(this._state(e.reason)?.state);
    const target = numericValue(this._state(e.target)?.state);
    const tier = cleanValue(this._state(e.tier)?.state);
    const tierId = this._tierId(e, tier);
    const presence = cleanValue(this._state(e.presence)?.state);
    const mainT = numericValue(a("main_temp"));
    const bedT = numericValue(a("bedroom_temp"));
    const hvac = cleanValue(a("hvac_action"));
    const cooling = hvac === "cooling";
    const regulating = cleanValue(a("regulating"));
    const enableReady = this._available(e.enable);
    const vacationReady = this._available(e.vacation);
    const prearrivalReady = this._callable(e.prearrival);
    const resumeReady = this._callable(e.resume);
    const enableOn = enableReady && this._state(e.enable)?.state === "on";
    const vacOn = vacationReady && this._state(e.vacation)?.state === "on";
    const preUntil = cleanValue(a("prearrival_until"));
    const holdActive = a("manual_hold_active");
    const holdUntil = holdActive ? a("manual_hold_until") : null;
    const holdVal = a("manual_hold_value");
    const holdTemp = Array.isArray(holdVal) && holdVal[0] === "temp" ? numericValue(holdVal[1]) : null;
    const nextT = a("next_transition");
    const unavailable = !effectiveReady;
    const off = eff === "disabled" || (enableReady && !enableOn);
    const bigTemp = holdTemp != null ? holdTemp : target;
    const bigLabel = holdTemp != null ? "Hold" : regulating === "bedroom" ? "Bedroom target" : "Target";
    const title = unavailable ? "Unavailable" : this._humanMode(eff);
    const subtitle = unavailable
      ? "Waiting for Climado to report its state"
      : this._humanReason(reason);

    return html`
      <ha-card class="${off ? "off" : ""} ${unavailable ? "unavailable" : ""}">
        <div class="head">
          <div>
            <div class="mode">${title}</div>
            <div class="reason">
              <span class="dot ${cooling ? "cool" : ""}"></span>${subtitle}
            </div>
          </div>
          <div class="temps">
            <div class="target">${this._temp(bigTemp)}</div>
            <div class="tlabel">${bigLabel}</div>
          </div>
        </div>

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
            <span class="rval ${cooling ? "cooling" : ""}">${cooling ? "Cooling" : hvac || "Idle"}</span>
            <span class="rlbl">AC</span>
          </div>
        </div>

        ${nextT && nextT.at
          ? html`<div class="next">Next: ${nextT.label} at ${this._fmt(nextT.at)}</div>`
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
            ? html`<span class="chip pre">pre-cool → ${this._fmt(preUntil)}</span>`
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
          <button class="action ghost" ?disabled=${!resumeReady} @click=${() => this._press(e.resume)}>
            <ha-icon icon="mdi:restart"></ha-icon><span>Resume</span>
          </button>
        </div>

        <div class="grid-title">Rate plan <span>tap an hour to change tier</span></div>
        ${this._grid("weekday", "Weekday")} ${this._grid("weekend", "Weekend / holiday")}

        <div class="legend">
          ${Object.entries(TIERS).map(
            ([id, t]) =>
              html`<span class="lg"><i style="background:${t.color}"></i>${t.name}</span>`
          )}
        </div>

        <div class="row">
          <button class="action" ?disabled=${!controlsReady} @click=${() => this._save()}>
            <ha-icon icon="mdi:content-save"></ha-icon><span>Save rate plan</span>
          </button>
          <button class="action ghost" @click=${() => this._initDraft(e)}>
            <ha-icon icon="mdi:restore"></ha-icon><span>Reset</span>
          </button>
        </div>
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
        disabled: "Off",
        unavailable: "Unavailable",
      }[m] || (m || "").replace(/_/g, " ")
    );
  }

  _humanReason(reason) {
    if (!reason) return "Waiting for Climado to report its state";
    const map = {
      vacation: "Vacation setback",
      manual_away: "Away (manual)",
      away: "Away — nobody home",
      pre_arrival: "Pre-cooling for your arrival",
      manual_hold: "Respecting your manual change",
      manual_sleep: "Sleep (manual)",
      "night/ecobee-sleep": "Overnight — cooling the bedroom",
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

  _fmt(iso) {
    try {
      return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return iso;
    }
  }

  _grid(profile, label) {
    const hours = this._draft[profile];
    const nowHour = new Date().getHours();
    return html`
      <div class="gwrap">
        <div class="glabel">${label}</div>
        <div class="bar">
          ${hours.map(
            (tier, h) => html`<div
              class="cell ${h === nowHour ? "now" : ""}"
              style="background:${TIERS[tier]?.color}"
              title="${h}:00 — ${TIERS[tier]?.name}"
              @click=${() => this._paint(profile, h)}
            ></div>`
          )}
        </div>
      </div>
    `;
  }

  static get styles() {
    return css`
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
        font-weight: 600;
        margin-top: 4px;
      }
      .grid-title span {
        font-weight: 400;
        color: var(--secondary-text-color);
        font-size: 0.8em;
        margin-left: 6px;
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
        border-radius: 6px;
        overflow: hidden;
      }
      .cell {
        cursor: pointer;
        border-right: 1px solid rgba(0, 0, 0, 0.12);
      }
      .cell.now {
        outline: 2px solid var(--primary-text-color);
        outline-offset: -2px;
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
      .computeLabel=${(s) => (s.name === "entity" ? "Climado mode entity" : "Thermostat (optional)")}
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

console.info("%c CLIMADO-CARD %c 0.3.9 ", "background:#1565c0;color:#fff", "");
