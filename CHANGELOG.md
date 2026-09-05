# Changelog

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
