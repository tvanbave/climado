# Climado

Presence‑aware, rate‑aware climate control for Home Assistant — an Alarmo‑style
custom integration that mimics how Nest/ecobee handle Home/Away and temperature
setting, with everything configurable from the UI (no YAML).

> **Status: M1 (MVP backend).** Single zone, cooling season, configured via the
> integration's options + standard entity cards. The bespoke Lovelace panel and
> the fully editable multi‑tier rate grid are later milestones (M2/M3).

## What it does
- **Home/Away/Sleep/Vacation** state machine driven by your chosen presence and
  occupancy sensors, with an auto‑away delay and a night away‑disable window.
- **Configurable TOU/ULO rate engine** — pre‑cool before the expensive period and
  coast through it. M1 ships the **Ontario ULO** layout with tunable on‑peak
  coast and pre‑cool lead/depth.
- **Night bedroom tracking** — drives the *bedroom* to its target even though the
  thermostat measures the main floor, via `target = bedroom_target − (bedroom − main_floor)`.
- **Manual pre‑arrival ("Heading home")** — a button/service that pre‑cools ahead
  of arrival, optionally only if the house has drifted warm; auto‑expires on
  arrival.
- **Alarmo‑style entity pickers** — thermostat, sensors and phones are all chosen
  from filtered lists; no entity IDs are hardcoded.

## Requirements
- An ecobee (or compatible single‑setpoint cooling) `climate` entity.
- **ecobee Hold Duration set to "Until you change it"** so HA holds persist.
- A `binary_sensor` workday sensor (optional) for weekend/holiday rate handling.

## Install (HACS custom repository)
1. HACS → ⋮ → **Custom repositories** → add this repo, category **Integration**.
2. Install **Climado**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Climado**, and pick your
   thermostat + sensors.

Manual install: copy `custom_components/climado/` into your HA `config/custom_components/`
and restart.

## Configuration
**Initial setup** (Add Integration) collects the thermostat, temp sensors,
presence (`device_tracker`/`person`), occupancy/motion (`binary_sensor`), workday
sensor, plus starting values for all setpoints/timeouts/rate knobs.

**After install**, every scalar setting lives as a **config-category `number`/`time`
entity on the device** (Settings → Devices → Climado → *Configuration*) — adjust
setpoints, away delay, night window + clamps, on-peak coast, pre-cool, and
pre-arrival inline, no dialogs, and use them on dashboards/automations. The
**options flow** (Configure) is slimmed to the structural entity pickers
(thermostat / sensors / presence / occupancy / workday).

### Priority ladder (how the setpoint is chosen)
`vacation` › `manual away` › `pre‑arrival` › `away` (daytime, or overnight only if the house was already empty at the night boundary) ›
`night bedroom‑tracking` (in the night window) › `home + rate offset` ›
home comfort. **Away wins over the rate overlay — coast never stacks on a setback.**

## Entities created
- `select.*_mode` — manual override (`auto`/`home`/`away`/`sleep`/`vacation`).
- `switch.*_climado_control` — master enable. `switch.*_vacation` — vacation hold.
- `button.*_heading_home_pre_cool`, `button.*_resume_clear_pre_cool`.
- `sensor.*` — effective mode, control reason, resolved target, rate tier, presence.
- `number.*` / `time.*` (Configuration category) — all tunables: setpoints, away delay, night window + clamps, on-peak coast, pre-cool, pre-arrival.

## Services
- `climado.start_pre_arrival` (`lead_minutes?`, `target?`, `only_if_above?`).
- `climado.clear_pre_arrival`.

## Defaults (parity with the prior `ulo_climate_controller` automation)
Home 23.5 · Away 28 · Bedroom target 23 · Away delay 45 min · Night 23:00–07:00 ·
Night clamp 19–25 · On‑peak coast +2.0 · Pre‑cool lead 90 min / depth 2.0 →
reproduces the 21.5 pre‑cool / 25.5 on‑peak behaviour.

## Lovelace card — `climado-card`
An Alarmo-style card: effective mode + reason, target vs. current temp, rate-tier
and presence chips, mode buttons (auto/home/away/sleep/vacation), enable +
vacation toggles, a **Heading home** pre-cool button, and a **TOU-style colored
weekly rate grid** (weekday + weekend/holiday) you can tap to recolor hours.

The card **ships with the integration and auto-loads** (served at
`/climado_static/climado-card.js`) — no `www` copy or resource entry needed. Just
add it to a dashboard:
```yaml
type: custom:climado-card
entity: select.climado_main_floor_mode   # any Climado entity; siblings auto-discovered
climate: climate.main_floor              # optional, shows current room temp
```
(If "Custom element doesn't exist" shows right after updating, hard-refresh the browser.)

The **control surface works against the M1 backend now** (it just calls the
select/switch/button services). The grid's **Save** calls `climado.set_rate_plan`,
which arrives with the **M2 backend** — until then the grid is a live
visualization + draft editor and Save shows a notice instead of persisting.

## Roadmap
- **M2** Generalized multi‑tier rate plan (arbitrary tiers/times, TOU preset);
  richer presence (per‑sensor debounce).
- **M3** Bespoke Lovelace panel incl. a TOU‑style colored rate grid editor.
- **M4** Multi‑zone; seasonal profiles; live price entity; geofence/temperature
  pre‑arrival; heating season.

## Verifying after deploy
With the integration loaded:
- Toggle a presence/occupancy sensor and watch `sensor.*_effective_mode` /
  `*_control_reason` and the thermostat setpoint react (away only after the delay).
- At night with phones away, confirm it stays in `night/bedroom`, not `away`.
- Press **Heading home** and confirm `pre_arrival` engages and expires on arrival.
