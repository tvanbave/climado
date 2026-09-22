# Changelog

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
