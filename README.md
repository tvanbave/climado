# Climado

Presence‑aware, rate‑aware climate control for Home Assistant — an Alarmo‑style
custom integration that mimics how Nest/ecobee handle Home/Away and temperature
setting, with everything configurable from the UI (no YAML).

> **Status: single zone, cooling season.** Backend + editable rate plan + shipped
> Lovelace card are done (M1/M2). The bespoke sidebar panel (M3) and multi‑zone /
> heating (M4) are future milestones.

## What it does
- **Home/Away/Sleep/Vacation** state machine driven by your chosen presence and
  occupancy sensors, with an auto‑away delay and a night away‑latch (away is only
  allowed overnight if the house was already empty at the night boundary).
- **Configurable TOU/ULO rate engine** — pre‑cool before the expensive period and
  coast through it. Ships the **Ontario ULO** layout; the weekly schedule is
  shown read-only on the card by default, and on‑peak coast / pre‑cool are tunable.
- **Native night handoff** — at the night window start, Climado activates the
  ecobee's own **Sleep comfort setting** (a true closed loop on your bedroom
  sensor that reaches target and cycles off). The overnight temperature is the
  ecobee Sleep comfort's setpoint — edit it in the ecobee app.
- **Manual pre‑arrival ("Heading home")** — a button/service that pre‑cools ahead
  of arrival; the button always engages, the service can be made conditional on
  the house having drifted warm. Auto‑expires on arrival.
- **Alarmo‑style entity pickers** — thermostat, sensors and phones are all chosen
  from filtered lists; no entity IDs are hardcoded.

## Requirements
- An ecobee (or compatible single‑setpoint cooling) `climate` entity.
- **ecobee Hold Duration set to "Until you change it"** so HA holds persist.
- The ecobee **Sleep comfort setting** assigned to your bedroom sensor (verify:
  during Sleep, the thermostat's displayed temperature should track the bedroom
  reading — if it shows the main‑floor value, remove + re‑add the sensor in the
  ecobee app's Sleep comfort).
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
`manual hold` (a hand adjustment on the thermostat is respected — no writes — until the next night‑window transition) ›
`night → ecobee Sleep comfort` (in the night window, or forced via the mode select) › `home + rate offset` ›
home comfort. **Away wins over the rate overlay — coast never stacks on a setback.**

### Manual adjustments
If someone changes the thermostat by hand (dial, ecobee app, or HA thermostat
card), Climado detects it and **respects it until the next night‑window edge**
(like ecobee's "hold until next transition"), then resumes control. Picking a
mode in the select, pressing **Resume**, or vacation/away/pre‑arrival supersede
the hold. Detection only arms in `auto` mode. A manual **Home** selection lasts
until the next night start, while **Sleep** lasts until the next night end;
Away and Vacation remain persistent until changed.

### Reliability and status
Departure timing and the current overnight Away decision survive restarts.
Away delay starts when the last configured presence/occupancy source leaves;
Away and pre-arrival expiry use scheduled deadlines.

Climado waits for thermostat commands to be confirmed by reported state. Service
errors retry after one minute; commands still unconfirmed after five minutes
are retried without becoming false manual holds. Manual-change detection is
paused while a command is pending, and for five minutes after a successful
service call to accommodate delayed thermostat reports.

The card refreshes when Home Assistant receives thermostat/temperature changes
and shows pending or retrying commands. Ecobee's own polling and sensor handoff
delays still apply. The effective-mode sensor also exposes `thermostat_target`,
`control_temperature`, `thermostat_updated_at`, `command_pending`, and
`command_error` for diagnostics. A pending Sleep target remains blank until
Ecobee reports the Sleep preset, rather than displaying the previous target.

## Entities created
- `select.*_mode` — override (`auto`/`home`/`away`/`sleep`/`vacation`). Home and
  Sleep return to Auto at their next day/night boundary.
- `switch.*_climado_control` — master enable. `switch.*_vacation` — vacation hold.
- `button.*_heading_home_pre_cool`, `button.*_resume_clear_pre_cool`.
- `sensor.*` — effective mode, control reason, resolved target, rate tier, presence.
- `number.*` / `time.*` (Configuration category) — tunables: setpoints, away delay, night window, on-peak coast, pre-cool, pre-arrival. (The overnight temperature is *not* here — it's the ecobee Sleep comfort's setpoint.)

## Services
- `climado.start_pre_arrival` (`lead_minutes?`, `target?`, `only_if_above?`, `force?`).
- `climado.clear_pre_arrival`.
- `climado.set_rate_plan` (`plan`: `{weekday, weekend}` of `[start, end, tier]` blocks; must cover 00–24 with no gaps/overlaps).

## Defaults
Home 23.5 · Away 28 · Away delay 45 min · Night 23:00–07:00 · On‑peak coast +2.0 ·
Pre‑cool lead 90 min / depth 2.0 → 21.5 pre‑cool / 25.5 on‑peak coast on the ULO layout.

## Lovelace card — `climado-card`
An Alarmo-style card: effective mode + reason, target vs. current temp, rate-tier
and presence chips, mode buttons (auto/home/away/sleep/vacation), enable +
vacation toggles, a **Heading home** pre-cool button, and a **TOU-style colored
rate timeline** that automatically shows weekday or weekend/holiday rates.

The card **ships with the integration and auto-loads** (served at
`/climado_static/climado-card.js`) — no `www` copy or resource entry needed. Just
add it to a dashboard:
```yaml
type: custom:climado-card
entity: select.climado_main_floor_mode   # any Climado entity; siblings auto-discovered
climate: climate.main_floor              # optional, shows current room temp
```
(If "Custom element doesn't exist" shows right after updating, hard-refresh the browser.)

For advanced custom schedules, enable `rate_editor: true`. Both schedules become
editable: tap hours to change tier, then **Save rate plan**
persists it via `climado.set_rate_plan` (weekday + weekend/holiday schedules). The
card reads the live plan back from the rate-tier sensor, and **Reset** reverts to
the saved plan. On-peak coast and pre-cool lead/depth remain device number entities.

## Roadmap
- Reliability first: regression tests and command/status diagnostics are included
  in 0.3.15. See [CHANGELOG.md](CHANGELOG.md) for the release details.
- **M2 [done]** Editable rate-plan schedules persisted via `climado.set_rate_plan`
  (arbitrary hour→tier over the 4 standard tiers). Future: custom tiers/ranks, TOU preset.
- **M3** Bespoke Lovelace panel incl. a TOU‑style colored rate grid editor.
- **M4** Multi‑zone; seasonal profiles; live price entity; geofence/temperature
  pre‑arrival; heating season.

## Verifying after deploy
With the integration loaded:
- Toggle a presence/occupancy sensor and watch `sensor.*_effective_mode` /
  `*_control_reason` and the thermostat setpoint react (away only after the delay).
- At the night start, confirm the reason becomes `night/ecobee-sleep` and the
  thermostat's displayed temperature tracks the **bedroom** sensor (if occupied
  at the boundary it must not go `away` overnight).
- Press **Heading home** and confirm `pre_arrival` engages and expires on arrival.

## Development checks
Use Python 3.14 for the current Home Assistant test runtime:

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q
ruff check --select E9,F63,F7,F82 custom_components tests
node --input-type=module --check < custom_components/climado/frontend/climado-card.js
node --test tests/card.test.mjs
```

The tests use Home Assistant's state machine, storage and coordinator with
simulated thermostat responses; they never control a live device.
