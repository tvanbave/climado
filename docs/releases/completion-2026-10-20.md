# Heating completion plan

Updated October 20, 2026. This supersedes the progress status in the original
0.4.0b1 checklist and heating plan. It is not permission to operate equipment.

## Installed and verified

- v0.4.0b3 installed. Manual Off/Cool/Heat and heat-pump/gas selection are shipped.
- Both heating sources started and stopped in the October 6 supervised tests.
  Owner confirmed warm air, outdoor operation for the heat pump, and outdoor
  shutdown for the furnace. Normal target cycling was not part of that test.
- HomeKit provides read-only local feedback; fuel identification remains cloud
  backed. Generic local "heating" cannot distinguish the heat pump from gas.
- Windows pause and stored restoration, separate heating targets, timed
  Home/Sleep overrides, heating alerts and advisory cost calculations exist.
- October 20 read-only HA check: Home, heating target 21 C, gas selected,
  fan-only operation reported, no pending command or heating alert. Bedroom
  and main-floor sensors both reporting; local feedback ready. This is a
  snapshot, not evidence of continuous correct operation.
- Outdoor candidate `sensor.gw2000b_outdoor_temperature` reports Celsius and
  was current at the audit. It has not been assigned to Climado's advisory.
- User reconfirmed ULO pricing. Furnace label confirms full product/model
  identifier `59TP6C080V171116`, consistent with the earlier furnace nameplate.
  This label is not the indoor coil model or proof of installation orientation.

## Built locally, not released or installed

- Shared indoor validation for card readings, conditional prearrival and
  heating alerts: Celsius, finite values, no future timestamp, latest HA report
  less than 15 minutes old. Invalid/stale readings have no numeric value.
- Exact indoor expiry refresh and filtered same-value report subscriptions.
  A valid identical report restores the reading. Sensor units changing also
  trigger a refresh. All listeners are removed on unload.
- Card keeps configured room labels visible with explicit unavailable/stale
  status. A missing room reading does not turn the thermostat off or change
  fuel; native Ecobee regulation continues. Explicit Heading home remains
  available; conditional prearrival rejects an unusable room reading.
- Disabled cost advice exposes a collapsed setup checklist. It never publishes
  prices or a preferred fuel while disabled, even if inputs are complete.
- Dated Q4 gas reference below, with boundary and component tests. No preset
  is automatically applied to a user's configuration.

HA report freshness is not proof of a new physical measurement: an upstream
integration can report cached data. Sleep sensor participation still needs
verification in Ecobee after the bedroom sensor was re-added.

Verification: 217 backend tests and 30 card tests passed, plus isolated browser
interaction/layout checks at 320, 390 and 1100 px. Mobile and desktop screenshots
were inspected. Critical lint and whitespace checks passed. These tests use
simulated equipment; no live settings were changed and no HA restart was made.

## Current rate research

The [OEB October Union notice](https://www.oeb.ca/sites/default/files/qram-union-20261001-en.pdf)
lists Union South M1 system-gas commodity 15.3548, supply adjustment 2.3461,
delivery 8.7022 / 8.3477 / 7.4325, and facility charge 0.0145 cents/m3.
The derived pre-tax marginal totals are:

| Billing-period volume | CAD/m3 |
| --- | --- |
| First 100 m3 | 0.264176 |
| Next 150 m3 | 0.260631 |
| Above 250 m3 | 0.251479 |

Exclude the fixed monthly charge and separate earnings/variance adjustment
paid in three equal instalments. Do not count transportation twice; the South
commodity charge includes it. The reference is bounded October 1 through
December 31, 2026 and rejected outside that interval. Confirm system-gas
service and rate zone against a bill before activation.

The [OEB October 16 announcement](https://www.oeb.ca/newsroom/2026/ontario-energy-board-announces-changes-electricity-prices-households-small-businesses)
sets November 1 ULO energy-only prices to 3.7 / 9.1 / 14.6 / 37.7 cents/kWh
(overnight / weekend off-peak / mid-peak / on-peak). OER changes to 15.7%.
Do not apply November prices in October. These are NOT complete marginal
electricity costs: variable delivery/regulatory charges, losses and applicable
tax/rebate treatment must be included consistently with gas.
[Oakville Hydro's rate page](https://www.oakvillehydro.com/accounts-customer-services/electricity-rates/electricity-rates-options)
still describes the current October period and earlier rebate. Use dated
sources, not an undated blend of the two periods. No electricity prices or
rebates were written into HA during this work.

## Next gates

1. Obtain the matched indoor coil label, furnace orientation, current electricity
   and gas billing rate details, and a defensible blower-energy allowance.
   Outdoor heat-pump model and furnace model are already known.
2. Confirm the matched-system COP table and its electrical-load boundary.
   Do not use seasonal efficiency or extrapolate outside verified data.
3. Populate current dated inputs and the outdoor sensor, then enable advisory
   only. Check all four ULO tiers, weekend/holiday handling, stale inputs,
   tariff expiry and the temperature range. Unknown gas usage must retain a
   conservative block-price range, not silently pick the cheapest block.
4. Verify heating Home/Away/Sleep and morning recovery, prearrival, Windows
   restoration for each source, restart/pending recovery and normal cycles.
   Use simulations for fault/low-temperature cases; supervise equipment tests.
5. Implement automatic handoffs only after installer/manufacturer limits and
   fallback behavior are agreed. Require confirmed shutdown, minimum on/off
   periods, source dwell, savings hysteresis, persisted transitions and manual
   override precedence. Do not treat economic advantage as capacity evidence.
6. Add optional fuel-aware preheating/peak optimization, complete regression
   and supervised tests, publish stable documentation and release.

Automatic seasonal changeover, multi-zone and learned performance models are
separate enhancements, not requirements for finishing this home's dual fuel.
