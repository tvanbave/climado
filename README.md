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

## Electricity Plan And Price
The card's **ULO / TOU** selector saves Climado's electricity plan. It changes
the rate timeline and cooling rate schedule, not the utility account's billing
plan. TOU automatically uses summer/winter schedules and the configured workday
sensor for holidays. ULO preserves existing custom schedule overrides, if any;
TOU uses the Ontario preset and leaves those saved overrides untouched.

The current price is the published **energy-only CAD/kWh** rate, displayed in
cents/kWh. It excludes delivery, tax and rebates. Dated prices cover November
2025 through October 2027, with the new prices applied on November 1, 2026;
outside that range the price is unavailable until updated. The price remains
visible when HVAC or Climado control is off.

Selecting TOU disables the temporary ULO fuel policy and keeps the current
heating source. Returning to ULO does not automatically re-enable it. ULO
heat-cost estimates are suppressed on TOU; they are separate from the published
energy price shown on the card. Plan changes wait until any source handoff ends.

## Roadmap

- **Next feature release: proposed v0.4.0** Heating profiles and dual-fuel
  readiness. Automatic heat-pump/gas selection is gated on verified equipment
  details and controls. See the [heating release plan](docs/releases/0.4.0-heating-plan.md)
  for scope, implementation order and acceptance checks. This is planned, not
  part of the current cooling-only release.
- Reliability first: regression tests and command/status diagnostics are included
  in 0.3.15. See [CHANGELOG.md](CHANGELOG.md) for the release details.
- **M2 [done]** Editable rate-plan schedules persisted via `climado.set_rate_plan`
  (arbitrary hour→tier over the 4 standard tiers). Future: custom tiers/ranks, TOU preset.
- **M3** Bespoke Lovelace panel incl. a TOU‑style colored rate grid editor.
- **M4** Multi‑zone; seasonal profiles; live price entity; geofence/temperature
  pre‑arrival; heating season.

## Verifying after deploy
### Windows open

Turn **Windows open** on in the card to pause heating and cooling while leaving
fan mode and minimum fan runtime unchanged. The pause survives Home Assistant
restarts and scheduled mode changes. Turn it off to restore the previous HVAC
mode and resume Climado's existing control behavior. A thermostat that was
already off stays off; Ecobee furnace-only selection is also preserved.

The toggle is a manual pause, with no automatic timeout or temperature override.
Resume, Home and Sleep do not cancel it; close the Windows open toggle explicitly.
Current temperatures and electricity rate remain visible, along with status
for pending or failed thermostat commands. Update through HACS, restart Home
Assistant and refresh the dashboard. No thermostat changes occur merely from
installing the feature.

#### Automatic ventilation pause (0.4.0b4)

In Climado options, select **Ventilation contacts** and enable **Automatically
pause for open windows / patio door**. Select only windows and the patio door,
not the front, side or garage doors. Monitoring is off by default.

- One selected contact must remain continuously open for five minutes before
  heating/cooling pauses. Brief openings and separate openings do not accumulate.
- All selected contacts must remain closed for two minutes before the automatic
  pause clears. Reopening or an unavailable contact cancels that countdown.
- **Windows open delay** (1-30 minutes) and **Windows closed delay** (1-15 minutes)
  are Configuration number entities on the Climado device.
- The card shows open-contact names and the pause/resume deadline. Its switch
  becomes **Manual windows pause** when automatic monitoring is enabled.
  That switch controls only the manual pause; the automatic pause remains until
  the contacts qualify as closed. An automatic closure never clears a manual hold.
- Monitoring, like the manual Windows pause, is independent of **Climado control**.
  Fan settings are unchanged. Restoration preserves an originally Off system,
  and restores the previous Heat/Cool mode and heating source otherwise.
- Pauses and the original mode survive restart. Open/closed countdowns restart
  from fresh observations, not from unobserved time while HA was down.
- A missing/unavailable contact is never treated as closed. During a pause it
  blocks automatic restoration; while unpaused it shows a warning without
  stopping HVAC solely because telemetry is missing.
- Disabling monitoring or clearing its contact list during an automatic pause
  transfers it to the manual toggle. Clear that toggle explicitly to resume.
  Cold-pause alerts remain in effect; they do not override an open-window pause.

See [automatic Windows validation](docs/releases/auto-windows-2026-10-20.md)
for the identified alarm-panel contacts and deployment checks.

During restoration, v0.4.0b5 labels the state **Restoring heating**
or **Restoring cooling**. A fresh local target matching the saved mode is shown
as **reported locally**, while the saved heating source is labeled **Restoring**
and awaiting Ecobee confirmation. These are display-only hints: the resolved
target stays unknown until confirmed, and HomeKit never identifies the fuel.
Stale, missing, wrong-mode or Off feedback does not supply a display target.

### Existing controls
With the integration loaded:
- Toggle a presence/occupancy sensor and watch `sensor.*_effective_mode` /
  `*_control_reason` and the thermostat setpoint react (away only after the delay).
- At the night start, confirm the reason becomes `night/ecobee-sleep` and the
  thermostat's displayed temperature tracks the **bedroom** sensor (if occupied
  at the boundary it must not go `away` overnight).
- Press **Heading home** and confirm `pre_arrival` engages and expires on arrival.

## Development checks
### Heating pre-release (0.4.0b1)

Version v0.4.0b1 is a heating pre-release, not a stable release. Live equipment
validation remains pending. It targets the tested HA 2026.9+ runtime.
Existing installations remain cooling-only unless heating targets are
explicitly enabled in Configure. Review the separate device Configuration
numbers first: Heating Home (20 C), Away (17 C), Vacation (15 C), and pre-arrival
(20 C). These are editable starting values, not confirmed preferences for your
home. Night control uses Ecobee's own Sleep heating target and assigned sensors.

Heating currently requires Celsius thermostat units. It follows the selected
`heat` mode and leaves fuel selection untouched; `heat_cool` is unsupported.
No heating rate offsets or automatic furnace fallback are provided. Turning
off heating support releases Climado's hold via Ecobee's native resume service;
that does not reset or change a manually selected Aux source.

The existing pre-arrival button ID is retained. In heating mode it uses the
heating target; the service accepts `only_if_below` instead of cooling's
`only_if_above`. Supplying a threshold for the wrong mode fails explicitly.
Conditional pre-arrival requires an available temperature; the button bypasses
that condition but not disabled control or unsupported thermostat modes.

Heating options now include an Auxiliary heat only switch picker. When omitted,
Climado discovers the enabled Ecobee Aux switch on the thermostat's device.
An unavailable switch reports Unknown; selected fuel and actual running
equipment are separate sensors. Heat/Aux changes clear stale target tracking.

The card now has separate **System mode** (Off / Cool / Heat) and **Heating
source** controls. These are distinct from the Auto/Home/Away/Sleep/Vacation
comfort profiles. With the system Off, **Next heating source** selects Heat
pump or Gas furnace locally without starting equipment; press Heat to start
that source. The draft defaults to Heat pump on a fresh card load and is not a
persisted backend preference. Turning off heating through this card retains
the observed source as its next local choice. The provisional ULO controller
below enables **Automatic** once Heat mode is confirmed.

Active systems must be switched Off before changing system mode or source.
Starts require supported modes, an available Aux switch for heating, and fresh
thermostat feedback (under 10 minutes) showing no active heating or cooling;
fan-only operation is allowed and fan settings are untouched. Controls are
blocked throughout a Windows open pause/restoration, except that the card can
prepare the next heating-source choice while both reported modes are Off and
no system request is pending. That choice sends no equipment command and does
not alter the mode/source saved for Windows restoration. Off remains available
to cancel a pending start, except during that Windows-owned pause.

Manual requests use `climado.set_system_mode` with `entry_id`, `hvac_mode`, and
`heat_source` (`heat_pump` or `gas`, required only for Heat). Gas uses Ecobee's
Aux ON command, which selects Aux-only and starts heating. Heat pump uses
explicit climate Heat, never Aux OFF because that can restore a previous
`heat_cool` mode. Reported state confirms commands; until confirmation, comfort
target writes are suspended. After five minutes an unconfirmed request reports
an error with no automatic retry. This is manual operation using Ecobee's own
equipment protections, not a validated automatic dual-fuel handoff controller.
Heating target automation remains separately opt-in; these manual mode controls
do not enable it. Older installed backends do not show unsupported controls.

Pending manual system requests are saved before sending and restored after an
HA restart without replaying the command. Storage failures prevent new mode
commands. Windows open cancels a pending manual request and retains pause
ownership. The card has expandable Heating targets using the existing number
entities, and distinguishes System off from Control disabled and Waiting.

### Optional local feedback (0.4.0b2)

After pairing the same Ecobee with HA's **HomeKit Device** integration, choose
its climate entity in Climado's **Configure > Local feedback thermostat** field.
Keep the existing Ecobee cloud climate entity as the primary thermostat and
retain its Auxiliary heat only switch. Leave the new field empty for cloud-only
operation. The two entities must represent the same physical thermostat.

The local entity is read-only. Climado continues sending every equipment,
target, and native Sleep command through the existing Ecobee integration.
Local reports must be available, under ten minutes old, and newer than the
request to acknowledge it. Fresh local Off/Cool reports can confirm those
mode requests; Heat also requires a new matching cloud Aux report. A generic
HomeKit `heating` action never identifies gas versus heat pump.

The card shows request progress and elapsed time, local mode confirmation
separately from fuel confirmation, observed activity, and the age/source of
feedback. Numeric targets can show **Target confirmed locally; syncing Ecobee**,
but native preset/hold confirmation remains cloud-owned. Losing local feedback
falls back to cloud data with a visible message. Contradictory mode readings
pause comfort writes; Off remains available. Starting another mode still requires
fresh cloud equipment-stopped feedback, plus stopped local feedback when available.
This improves visibility and some confirmations, not the Ecobee cloud's speed.

After a command, Climado requests a status read after about two seconds and
once more about three minutes later, within a five-minute window. Requests are
coalesced across commands and spaced at least three minutes apart. The public
`homeassistant.update_entity` service respects integration throttling, so it
does not guarantee a new cloud report. Refresh failures are separate from
command failures and never cause an additional HVAC command. Existing comfort
target retry behavior is unchanged. Unloading cancels pending reads.

### Heating alerts

Monitoring and HA notifications default to enabled and can be independently
disabled in Configure. Thresholds are Configuration number entities:

- Windows open and any fresh configured indoor sensor below 16 C for 10 minutes.
- Thermostat command failure or a manual mode request unconfirmed after five minutes.
- Reported continuous heating for 60 minutes with less than 0.3 C rise, while
  still more than 0.5 C below target.

Checks run at least once a minute while heating or paused. Temperature checks
require Celsius readings reported within 15 minutes. Progress monitoring uses
the thermostat's control temperature; a source/target/preset/sensor change, stop,
restart or observation gap resets its baseline. These checks flag symptoms, not
a diagnosis of equipment failure. Short heating cycles do not accumulate toward
the continuous-run check. Missing readings are shown as monitoring unavailable.
Notifications are emitted once per active issue, dismissed on resolution, and
can be muted without hiding card alerts. They appear in HA, not as phone push.
Alerts never resume HVAC, switch fuel or cancel Windows open. They are not an
independent freeze-protection system and cannot run while HA is down.

### Cost observations

Choose **Configure heating cost estimates** in the options flow to enter dated
ULO electricity prices, marginal gas-price blocks, gas energy conversion,
furnace efficiency, blower electricity and a matched-system COP table. Table
rows have native numeric fields; no configuration.yaml edits are needed. The
last gas block has no upper limit, and COP rows must be ordered by increasing
outdoor temperature. Valid-until dates are exclusive. Incomplete setup can be
saved, and the card names the missing inputs. No prices or COP values are
silently selected for the installation.

Select an outdoor Celsius sensor in the main options. Its last reported time,
rather than its last value change, determines freshness. Changed readings and
exact rate boundaries update estimates; a one-minute freshness check handles
unchanged reports and stale-data recovery. Unknown billing-period gas usage
produces a cost range. The optional gas sensor must track total consumption for
the current billing period in m3, including other gas appliances, and reset at
the correct billing boundary. Stale/invalid usage widens the range rather than
choosing an unsupported price block.

The card shows selected fuel, observed equipment, estimated COP and delivered
heat costs in CAD cents/kWh. Recommendations use a configurable savings margin
and never change fuel. Costs remain available while Climado control is off or
Windows open is active. Expired tariffs, stale outdoor readings and temperatures
outside the supplied COP table clear cost estimates. Use consistent tax/rebate
bases, include variable charges, and exclude fixed monthly charges.

The recommendation sensor includes its reason, selected source and an
observation-only flag. HA Recorder can retain this history if the entity is not
excluded; INFO logs also record changes to recommendation/status/reason/source
without logging every unchanged sample. No estimated savings or fuel commands
are derived from missing inputs. See `docs/releases/0.4.0b1-checklist.md` for the
publication and supervised equipment checks that remain.

`fuel_policy.py` remains a pure simulation for future measured-cost selection.
The separate provisional ULO controller below does not consume advisory prices
or claim verified savings. Matched-coil performance and automatic comfort
fallback remain future work.

### Provisional ULO fuel control (0.4.0b6)

Configure an **Outdoor temperature sensor** in Climado options, enable heating
targets and Climado control, and confirm Heat mode. Then press **Automatic** in
the card's Heating source row. Press it again to disable this policy. The
`climado.set_fuel_automation` service accepts `entry_id` and `enabled`.

- During the configured ultra-low tier, choose gas at or below **2 C**, and
  choose the heat pump above **4 C**. Between those boundaries, keep the source.
- During all other tiers, including weekend daytime, choose gas.
- Start a handoff only after 30 minutes on the selected source and after the
  current heating cycle ends. This is a preference, not an immediate cutoff.
- Confirm Off and idle equipment, wait five continuous minutes with valid
  feedback, then send one start command. Recheck weather and tier before start.
  Ecobee confirms fuel; generic HomeKit Heat never confirms the heating source.
- Fresh Celsius outdoor data (under 15 minutes) and mode/fuel data (under 10
  minutes) are required. Missing data holds the current source. If local
  feedback is configured, it must also be fresh and agree with cloud mode.
- Windows pause and disabled Climado heating control take precedence. Explicit
  manual system commands disable Automatic. A manual fuel/mode change observed
  outside a transition also disables it.
- Requests are persisted before sending, with no automatic command retry.
  Restart during a handoff or an interrupted/failed handoff disables Automatic
  and raises an alert; the system may remain Off and needs manual attention.
  Restart outside a handoff restores the choice with a new 30-minute dwell.

This temporary rule uses the agreed November-rate assumption, not a verified
installed-system COP curve. It does not automatically change with tariffs.
There is no automatic fallback for inadequate heat output yet; heating alerts
remain active. Fan settings and comfort profiles are unchanged. Supervised live
handoff validation is required after installation.

### Test commands

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
