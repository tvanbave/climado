# Changelog

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
