"""Selected fuel, actual equipment and pure switching policy are independent."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.climado.fuel import find_aux_entity, running_source
from custom_components.climado.fuel_policy import decide_fuel


@pytest.mark.parametrize("equipment,action,expected", [
    ("heatPump,fan", "heating", "heat_pump"), ("auxHeat2,fan", "heating", "gas"),
    ("fan", "fan", "fan"), ("", "idle", "idle"), (None, "heating", "unknown"),
    ("", "heating", "unknown"), ("futureToken,fan", "heating", "unknown"),
    ("heatPump,auxHeat1,fan", "heating", "mixed"), ("compCool1,fan", "cooling", "cooling"),
])
def test_only_reported_equipment_proves_running_source(equipment, action, expected):
    assert running_source(equipment, action) == expected


async def test_real_aux_switch_change_invalidates_pending_heat_command(engine):
    e = engine
    e.c._aux_entity = "switch.aux"
    e.entry.options["heating_enabled"] = True
    e.hass.states.async_set("switch.aux", "off")
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", "heat", {**old.attributes, "temperature_unit": "°C", "equipment_running": ""})
    e.service.side_effect = RuntimeError("offline")
    await e.c._async_update_data()
    assert e.c._pending_command
    assert e.c._fuel_context() == ("heat", False)
    e.hass.states.async_set("switch.aux", "on")
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", "heat", {**old.attributes, "temperature": 20})
    e.service.reset_mock()
    data = await e.c._async_update_data()
    assert data["selected_source"] == "gas"
    assert data["running_source"] == "idle"
    assert not data["command_pending"]
    e.service.assert_not_called()
    e.hass.states.async_set("switch.aux", "unavailable")
    assert (await e.c._async_update_data())["selected_source"] == "unknown"


async def test_aux_and_outdoor_are_watched(engine, monkeypatch):
    e = engine
    e.c._aux_entity = "switch.aux"
    e.entry.options.update(outdoor_temp_sensor="sensor.outdoor", heat_cost={"gas_usage_sensor": "sensor.gas_bill"})
    watcher = Mock(return_value=Mock())
    monkeypatch.setattr("custom_components.climado.coordinator.async_track_state_change_event", watcher)
    await e.c.async_setup_listeners()
    assert {"switch.aux", "sensor.outdoor", "sensor.gas_bill"} <= set(watcher.call_args.args[1])


async def test_discovery_filters_switch_platform_and_disabled_entries(engine, monkeypatch):
    registry = SimpleNamespace(async_get=lambda _: SimpleNamespace(device_id="ecobee-device"))
    entries = [SimpleNamespace(domain="switch", platform=platform, unique_id=uid, entity_id=entity, disabled_by=disabled)
               for platform, uid, entity, disabled in (("ecobee", "test_aux_heat_only", "switch.aux", None),
                                                       ("other", "other_aux_heat_only", "switch.other", None),
                                                       ("ecobee", "disabled_aux_heat_only", "switch.disabled", "user"))]
    monkeypatch.setattr("custom_components.climado.fuel.er.async_get", lambda _: registry)
    lookup = Mock(return_value=entries)
    monkeypatch.setattr("custom_components.climado.fuel.er.async_entries_for_device", lookup)
    assert find_aux_entity(engine.hass, "climate.test") == "switch.aux"
    lookup.assert_called_once_with(registry, "ecobee-device")


def decision(clock, **overrides):
    return decide_fuel(**{
        "current": "gas", "preferred": "heat_pump", "eligible": {"gas", "heat_pump"},
        "now": clock.now(), "last_changed": clock.now() - timedelta(hours=1),
        "minimum_dwell": timedelta(minutes=30), "enabled": True, "inputs_valid": True, **overrides,
    })


@pytest.mark.parametrize("overrides,reason", [
    ({"enabled": False}, "automation_disabled"), ({"paused": True}, "windows_open"),
    ({"manual_override": True}, "manual_override"), ({"transition_pending": True}, "transition_pending"),
    ({"inputs_valid": False}, "inputs_unavailable"), ({"current": "unknown"}, "selection_unknown"),
    ({"preferred": None}, "within_savings_margin"), ({"eligible": {"gas"}}, "source_ineligible"),
    ({"last_changed": None}, "dwell_unknown"),
])
def test_policy_blocks_unverified_or_unwanted_switches(clock, overrides, reason):
    result = decision(clock, **overrides)
    assert not result.switch
    assert result.reason == reason


def test_policy_dwell_boundary_and_confirmed_transition(clock):
    changed = clock.now() - timedelta(minutes=29, seconds=59)
    assert decision(clock, last_changed=changed).reason == "minimum_dwell"
    clock.value += timedelta(seconds=1)
    assert decision(clock, last_changed=changed).switch
    assert not decision(clock, transition_pending=True).switch
    assert not decision(clock, current="heat_pump", last_changed=clock.now()).switch


def test_comfort_requirement_can_override_cost_but_not_eligibility(clock):
    result = decision(clock, current="heat_pump", required_source="gas")
    assert result.source == "gas" and result.switch and result.reason == "comfort_required"
    assert not decision(clock, current="heat_pump", required_source="gas", eligible={"heat_pump"}).switch
