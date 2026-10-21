# Changelog

## 0.4.0b8 - Automatic Fuel Status

- Show the selected heat pump or gas furnace prominently below Automatic,
  with a separate Heating, Idle or Fan only status.
- Distinguish source changeovers, windows pauses and unconfirmed source feedback.
- Display-only change; fuel policy and thermostat commands are unchanged.

## 0.4.0b7 - Electricity Plan And Energy Price

- Add a persisted ULO/TOU selector and current published CAD energy price/kWh
  to the card, plus a standalone electricity energy price sensor.
- Apply Ontario TOU summer/winter and weekend/holiday schedules, including exact
  boundary refreshes and dated November 1, 2026 prices. Exclude delivery, tax and
  rebates from the clearly labelled energy-only price.
- Selecting TOU disables the provisional ULO fuel policy without changing HVAC
  mode; ULO-specific heat-cost advice is suppressed. Plan changes are blocked
  during a source handoff. Utility account billing is not changed.

## 0.4.0b6 - Provisional ULO Fuel Control

- Add opt-in Automatic fuel selection: gas at or below 2 C and heat pump above
  4 C during ultra-low hours, retaining the source between thresholds. Prefer
  gas in all other tiers.
- Wait for the heating cycle to finish, enforce 30-minute source dwell and a
  five-minute confirmed Off interval, then confirm the requested fuel.
- Require fresh outdoor, thermostat and fuel feedback; retain Windows pause,
  manual override and disabled-control precedence. Failures and interrupted
  handoffs require manual attention and never replay equipment commands.
- Add card status and regression tests. Live installation/validation pending.

## 0.4.0b5 - Clearer Window Restoration Status

- Show a fresh, matching HomeKit target as locally reported during Windows
  restoration without publishing it as a resolved Climado target.
- Show the saved heating source as restoration intent awaiting Ecobee
  confirmation, never as confirmed fuel or running equipment.
- Replace the Windows open heading with Restoring heating/cooling while
  restoration is pending. Equipment commands and confirmation guards are unchanged.

## 0.4.0b4 - Automatic Windows, Sensor Reliability and Cost Setup

- Add opt-in ventilation contact monitoring with adjustable continuous-open
  and all-closed delays (five and two minutes by default), exact timer wakeups,
  separate persisted manual/automatic pause ownership, and card status.
- Preserve the existing HVAC mode, heating source and fan settings. Unknown
  contacts prevent automatic restoration; disabling monitoring during a pause
  leaves a manual pause rather than unexpectedly starting equipment.
- Reject stale, future-dated, non-finite and non-Celsius room observations in
  card status, conditional prearrival and heating alerts. Refresh at the
  15-minute expiry and recover on identical fresh reports.
- Keep room labels visible with a stale/unavailable status instead of displaying
  old temperatures. Native thermostat regulation and manual fuel choice remain
  unchanged.
- Add a collapsed heating-cost setup checklist while advice is disabled, without
  publishing estimates or a preferred source.
- Record the dated October-December 2026 Union South M1 marginal gas reference
  and update the completion plan. No new cost inputs are automatically installed.

## 0.4.0b3 - Source Selection While Paused

- Allow choosing the next heating source while the system is Off, including
  during a Windows open pause. This changes only the card's draft selection;
  starting Heat still requires an explicit action and all existing safety guards.

## 0.4.0b2 - Local Thermostat Feedback

- Add an optional read-only HomeKit feedback thermostat. Fresh local reports
  can confirm system modes and show target acknowledgements sooner; all writes
  still use the primary Ecobee integration. Generic local heating never proves
  which fuel is selected or running.
- Keep cloud equipment-stop guards, require a new Aux report to confirm a local
  heating request's fuel, and pause comfort writes while local/cloud modes differ.
  Missing or stale local reports fall back to existing cloud behavior.
- Add bounded post-command status refreshes through HA's public update service,
  spaced at least three minutes apart without bypassing Ecobee's throttle.
  Read failures do not trigger extra equipment commands.
- Show sending, elapsed confirmation time, local mode versus fuel confirmation,
  current local activity, feedback age and fallback status in the card.

## 0.4.0b1 - Heating Pre-release

Requires Home Assistant 2026.9 or newer. Heating is opt-in; automatic fuel
switching is not enabled. Live equipment validation remains pending.

- Persist pending manual system commands before sending them. Recover the
  confirmation guard after restart without replaying equipment commands.
- Add configurable cold-window-pause, command-failure and poor-heating-progress
  alerts, with card messages and optional deduplicated HA notifications. Alerts
  never change HVAC or fan settings. Stale readings and observation gaps reset
  temperature-monitoring windows.
- Add expandable heating targets to the card; distinguish System off from
  disabled Climado control and waiting for a mode command.
- Keep the rate timeline visible when the mobile layout stacks its label above
  the bar; prevent flex sizing from collapsing the bar's height.
- Record advisory fuel recommendation changes with reasons and selected source;
  recommendations remain observation-only and require valid configured inputs.
- Align integration/card versions at 0.4.0b1. Set the candidate's HA minimum to
  the tested 2026.9 runtime; older runtime compatibility is not claimed.

- Add separate System mode (Off/Cool/Heat) and heating-source controls to the
  card. Choose the next source while Off, then explicitly start Heat. Automatic
  fuel selection remains disabled. Manual commands target one Climado entry,
  require fresh idle feedback before starting, respect Windows pauses, and wait
  for reported confirmation without automatic retries or fan changes.
- Track Ecobee's configured or same-device Auxiliary heat only switch, clear
  stale command state on fuel changes, and report selected fuel separately from
  observed equipment. Share Aux discovery with the Windows open pause.
- Add an optional heat-cost configuration form with dated ULO prices, gas
  billing blocks, efficiency, blower consumption and matched-system COP rows.
- Connect the configured outdoor sensor and current rate tier to advisory
  cost sensors and a card display. Missing, stale, expired and invalid inputs
  clear estimates with an explicit reason. Recommendations issue no fuel commands.
- Add a separately tested, simulation-only fuel policy for eligibility,
  cost advantage, minimum dwell, manual overrides and pending transitions.
- Add opt-in Celsius heating targets for Home, Away, Vacation and pre-arrival,
  retaining native Sleep and existing cooling configuration identifiers.
- Preserve manual Heat/Aux selection. Heating rate offsets and automatic fuel
  selection are not enabled in this milestone.
- Cancel stale command tracking and pre-arrival on thermostat mode changes;
  report unsupported/off modes explicitly. Release the heating hold on opt-out.
- Add heating-aware card labels and the `only_if_below` pre-arrival parameter.
- Add a separately tested advisory cost calculation module: temperature-based
  COP interpolation, dated gas tariffs, consumption blocks, price comparison,
  furnace auxiliary electricity and input/freshness validation. This module is
  connected to the optional advisory settings and sensors. Automatic fuel
  switching and manufacturer-specific equipment eligibility remain deferred.

## 0.3.17

- Correct the dashboard resource cache version so browsers fetch the new
  Windows open card after updating. No thermostat behavior changes.
- Add a regression test requiring the integration manifest, resource version
  and card version to match.

Includes all v0.3.16 Windows open features. Restart Home Assistant after updating
and refresh the dashboard.

## 0.3.16

- Add a Windows open switch to the Climado card. It pauses heating/cooling
  without changing fan settings or the Climado control switch.
- Remember the previous HVAC mode across restarts and restore it when Windows
  open is switched off, including Ecobee furnace-only selection. A thermostat
  that was already off stays off.
- Keep temperatures and the current electricity rate visible while paused.
  Scheduled modes, Resume and Heading home cannot cancel the window pause.
- Confirm thermostat commands from reported state, retry failures, and handle
  delayed acknowledgements when the toggle is changed quickly.

This is an explicit manual pause with no automatic timeout or temperature
override. The fall heating and automatic fuel-selection work is not included.
Restart Home Assistant after updating and refresh the dashboard for the new card.

## 0.3.15

- Retry failed thermostat commands without treating them as manual holds. Wait
  for the reported preset/temperature to match before marking a command applied.
- Exit Sleep correctly when its setpoint equals the Home target.
- Preserve departure timing and the overnight Away decision across restarts.
  Startup presence sensors that are unavailable do not erase saved timing.
- Update room temperatures, target and AC status when Home Assistant receives
  thermostat or sensor changes. Display pending/retrying commands on the card.
- Start Away delay at departure, and schedule Away and pre-arrival expiry at
  their deadlines. Expired pre-arrival indicators are cleared.
- Retry a failed handoff to the thermostat's native program when disabling
  Climado. Serialize overlapping evaluations to avoid duplicate commands.
- Add backend regression tests using Home Assistant 2026.9.0 and card tests.

After updating through HACS, restart Home Assistant and refresh the dashboard.
Ecobee's own reporting and sensor-transition delays still apply; this release
removes the additional delay caused by Climado's periodic status snapshots.
