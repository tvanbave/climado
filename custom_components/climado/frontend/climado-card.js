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
  auto: "🟢",
  home: "🏠",
  away: "🌙",
  sleep: "🛏️",
  vacation: "🧳",
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

  _attr(id, key) {
    const s = this._state(id);
    return s && s.attributes ? s.attributes[key] : undefined;
  }

  _backendPlan(e) {
    const attr = e && this._state(e.tier)?.attributes?.plan;
    return attr || this._config.rate_plan || DEFAULT_PLAN;
  }

  // ---- actions ----
  _setMode(e, mode) {
    this.hass.callService("select", "select_option", {
      entity_id: e.mode,
      option: mode,
    });
  }

  _toggle(entity) {
    if (!entity) return;
    this.hass.callService("switch", "toggle", { entity_id: entity });
  }

  _press(entity) {
    if (!entity) return;
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

    const mode = this._state(e.mode)?.state || "auto";
    const eff = this._state(e.effective_mode)?.state || "—";
    const reason = this._state(e.reason)?.state || "";
    const target = this._state(e.target)?.state;
    const tier = this._state(e.tier)?.state || "";
    const presence = this._state(e.presence)?.state || "";
    const current = e.climate ? this._attr(e.climate, "current_temperature") : undefined;
    const enableOn = this._state(e.enable)?.state === "on";
    const vacOn = this._state(e.vacation)?.state === "on";
    const preUntil = this._attr(e.effective_mode, "prearrival_until");
    const holdUntil = this._attr(e.effective_mode, "manual_hold_active")
      ? this._attr(e.effective_mode, "manual_hold_until")
      : null;

    return html`
      <ha-card>
        <div class="head">
          <div>
            <div class="mode">${eff.replace(/_/g, " ")}</div>
            <div class="reason">${reason}</div>
          </div>
          <div class="temps">
            <div class="target">${target != null ? `${target}°` : "—"}</div>
            ${current != null ? html`<div class="cur">now ${current}°</div>` : ""}
          </div>
        </div>

        <div class="chips">
          <span class="chip" style="--c:${TIERS[this._tierId(e, tier)]?.color || "#999"}">
            ${tier || "tier"}
          </span>
          <span class="chip ${presence === "occupied" ? "ok" : "warn"}">${presence}</span>
          ${preUntil
            ? html`<span class="chip pre">pre-cooling until ${this._fmt(preUntil)}</span>`
            : ""}
          ${holdUntil
            ? html`<span class="chip hold">manual hold until ${this._fmt(holdUntil)}</span>`
            : ""}
        </div>

        <div class="modes">
          ${MODES.map(
            (m) => html`<button
              class="modebtn ${mode === m ? "sel" : ""}"
              @click=${() => this._setMode(e, m)}
            >
              <span>${MODE_ICON[m]}</span>${m}
            </button>`
          )}
        </div>

        <div class="row">
          <label class="tgl">
            <ha-switch .checked=${enableOn} @change=${() => this._toggle(e.enable)}></ha-switch>
            Climado control
          </label>
          <label class="tgl">
            <ha-switch .checked=${vacOn} @change=${() => this._toggle(e.vacation)}></ha-switch>
            Vacation
          </label>
        </div>

        <div class="row">
          <button class="action" @click=${() => this._press(e.prearrival)}>
            🏠⏱ Heading home
          </button>
          <button class="action ghost" @click=${() => this._press(e.resume)}>
            ↺ Resume
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
          <button class="action" @click=${this._save}>Save rate plan</button>
          <button class="action ghost" @click=${() => this._initDraft(e)}>Reset</button>
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
        padding: 14px;
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
      }
      .mode {
        font-size: 1.5em;
        font-weight: 600;
        text-transform: capitalize;
      }
      .reason {
        color: var(--secondary-text-color);
        font-size: 0.85em;
      }
      .temps {
        text-align: right;
      }
      .target {
        font-size: 1.6em;
        font-weight: 600;
      }
      .cur {
        color: var(--secondary-text-color);
        font-size: 0.85em;
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
        gap: 2px;
        padding: 8px 0;
        border: 1px solid var(--divider-color);
        border-radius: 10px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        cursor: pointer;
        text-transform: capitalize;
        font-size: 0.8em;
      }
      .modebtn.sel {
        border-color: var(--primary-color);
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
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
        padding: 10px;
        border: none;
        border-radius: 10px;
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
        cursor: pointer;
        font-size: 0.9em;
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

console.info("%c CLIMADO-CARD %c 0.3.6 ", "background:#1565c0;color:#fff", "");
