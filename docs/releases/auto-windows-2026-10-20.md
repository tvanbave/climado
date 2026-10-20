# Automatic Windows Pause

## Status

Implemented locally; not released, installed or enabled in Home Assistant.
Home Assistant discovery was read-only. No real equipment was operated.

## Contacts Identified

ESPHome device: Alarm Panel Pro 97b758, model alarm-panel-pro.
All four contacts reported closed during discovery on October 20, 2026:

| Name | Entity |
| --- | --- |
| Patio Door | `binary_sensor.konnected_40f52097b758_patio_door` |
| Dining Room Window | `binary_sensor.konnected_40f52097b758_dining_room_window` |
| Basement Windows | `binary_sensor.konnected_40f52097b758_basement_windows` |
| Basement Windows 2 | `binary_sensor.konnected_40f52097b758_basement_windows_2` |

Front Door, Side Door, Garage Door and Main Floor Motion are excluded from this
proposed selection. Do not substitute the separate Patio Door Sensor Door.
The selected entities must be configured after installation; no household IDs
are hardcoded or selected automatically in the integration.

## Behavior

Default delays are five minutes continuously open on any one selected contact,
and two minutes continuously closed on every selected contact. Contact edges
are tracked before debounced coordinator evaluations. Deadlines use the existing
exact boundary scheduler and are recomputed after state or delay changes.

The persistent Windows pause has separate manual and automatic owners.
Only when both are clear can its existing confirmed restoration proceed.
Legacy saved pauses migrate to manual ownership. Manual switch entity IDs
are unchanged, and the card labels that switch as Manual windows pause when
monitoring is enabled. An automatic pause appears separately in status.

Unknown telemetry resets qualification and prevents automatic restoration.
On restart the ownership and original mode are retained, but countdowns
requalify from fresh observations. Disabling monitoring or removing all
contacts while automatically paused retains a manual hold for explicit release.

No fan changes, automatic fuel switching, forced heating, or temperature-based
override is added. Existing low-temperature alerts remain relevant.

## Local Validation

Simulated tests cover exact thresholds, brief patio openings, ignored other
doors, alternating contacts, edges inside debounce, reopening during closure,
unknown/unavailable/missing contacts, tunable delays, independent manual holds,
restart, legacy migration, monitoring removal, failed writes and pending Off
confirmation. Restoring Cool, Heat pump, Gas furnace and already-Off states is
covered without operating physical equipment.

Card tests cover manual/automatic labels, names, countdown messages and unknown
contacts. Browser checks exercise the manual switch with simulated services and
verify overflow at 320, 390 and 1100 pixel widths.

## Deployment Checks Still Required

1. Publish/install an approved release and restart Home Assistant.
2. Select the four contacts above in Climado options and enable monitoring.
3. With a person present, verify that each selected contact really reports open
   and closed correctly. Confirm the basement zone coverage.
4. Confirm a brief patio trip does not pause, while a sustained opening pauses
   at the configured deadline, subject to thermostat command/confirmation latency.
5. Close every monitored opening and confirm restoration after two minutes.
6. Confirm a manual hold remains after closure, and that neither fuel selection
   nor minimum fan runtime is changed. Repeat using each source when appropriate.

Any test that could start heating/cooling needs explicit approval and normal
observation. Do not fabricate live contact states or disconnect the alarm panel.
