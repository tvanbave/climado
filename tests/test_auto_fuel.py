"""ULO rules and real coordinator handoffs with simulated HVAC services only."""
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.climado.auto_fuel import ulo_source
from custom_components.climado import _register_services
from custom_components.climado.coordinator import ClimadoCoordinator


def report(e, source="gas", mode="heat", outdoor=6, equipment="", action="idle"):
    e.c._aux_entity = "switch.ecobee_aux"
    e.entry.options.update(aux_heat_switch="switch.ecobee_aux", heating_enabled=True,
                           outdoor_temp_sensor="sensor.outdoor")
    attrs = dict(e.hass.states.get("climate.test").attributes)
    attrs.update(hvac_modes=["off", "heat", "cool"], hvac_action=action,
                 equipment_running=equipment, temperature=20, preset_mode="temp", fan_min_on_time=30)
    stamp = e.clock.utcnow().timestamp()
    e.hass.states.async_set("climate.test", mode, attrs, timestamp=stamp)
    e.hass.states.async_set("switch.ecobee_aux", "on" if source == "gas" else "off", timestamp=stamp)
    e.hass.states.async_set("sensor.outdoor", str(outdoor), {"unit_of_measurement": "°C"}, timestamp=stamp)
    e.hass.services.async_register("climate", "set_hvac_mode", AsyncMock())
    e.hass.services.async_register("switch", "turn_on", AsyncMock())


async def arm(e, source="gas", outdoor=6):
    e.clock.set("2026-11-02T23:00:00")
    e.service.side_effect = None
    report(e, source, outdoor=outdoor)
    await e.c.auto_fuel.set_enabled(True)


@pytest.mark.parametrize("current,temp,expected", [
    ("heat_pump", 2, "gas"), ("heat_pump", 1.9, "gas"),
    ("heat_pump", 2.1, "heat_pump"), ("gas", 3, "gas"),
    ("gas", 4, "gas"), ("gas", 4.01, "heat_pump"),
])
def test_exact_hysteresis_edges(current, temp, expected):
    assert ulo_source(current, "ultra_low", temp) == expected


@pytest.mark.parametrize("tier", ["off_peak", "mid_peak", "on_peak"])
def test_every_non_overnight_tier_uses_gas(tier):
    assert ulo_source("heat_pump", tier, 15) == "gas"


@pytest.mark.parametrize("source,outdoor,target", [("gas", 6, "heat_pump"), ("heat_pump", 2, "gas")])
async def test_handoff_requires_dwell_off_confirmation_rest_and_fuel_confirmation(engine, source, outdoor, target):
    e = engine
    await arm(e, source, outdoor)
    assert not await e.c.auto_fuel.step("ultra_low")
    e.service.assert_not_called()
    e.clock.value += timedelta(minutes=30)
    report(e, source, outdoor=outdoor)
    assert await e.c.auto_fuel.step("ultra_low")
    assert e.service.call_args.args[:3] == ("climate", "set_hvac_mode", {"entity_id": "climate.test", "hvac_mode": "off"})
    assert await e.c.auto_fuel.step("ultra_low")
    assert e.service.await_count == 1
    e.clock.value += timedelta(seconds=1)
    report(e, source, "off", outdoor)
    e.c.system.confirm()
    assert not e.c.system.pending
    assert await e.c.auto_fuel.step("ultra_low")
    e.clock.value += timedelta(minutes=4, seconds=59)
    report(e, source, "off", outdoor)
    assert await e.c.auto_fuel.step("ultra_low")
    assert e.service.await_count == 1
    e.clock.value += timedelta(seconds=1)
    report(e, source, "off", outdoor)
    assert await e.c.auto_fuel.step("ultra_low")
    assert e.service.await_count == 2
    expected = ("switch", "turn_on", {"entity_id": "switch.ecobee_aux"}) if target == "gas" else (
        "climate", "set_hvac_mode", {"entity_id": "climate.test", "hvac_mode": "heat"})
    assert e.service.call_args.args[:3] == expected
    assert await e.c.auto_fuel.step("ultra_low")
    e.clock.value += timedelta(seconds=1)
    report(e, target, outdoor=outdoor)
    e.c.system.confirm()
    assert not await e.c.auto_fuel.step("ultra_low")
    assert e.c.auto_fuel.expected == target
    assert e.c.auto_fuel.phase is None
    assert e.hass.states.get("climate.test").attributes["fan_min_on_time"] == 30


async def test_stale_outdoor_or_running_equipment_never_stops_heat(engine):
    e = engine
    await arm(e)
    e.clock.value += timedelta(minutes=30)
    assert not await e.c.auto_fuel.step("ultra_low")
    report(e, equipment="auxHeat1", action="heating")
    assert not await e.c.auto_fuel.step("ultra_low")
    assert "cycle" in e.c.auto_fuel.reason
    report(e, outdoor="nan")
    assert not await e.c.auto_fuel.step("ultra_low")
    e.service.assert_not_called()


async def test_seven_am_changes_desired_source_and_schedules_boundary(engine):
    e = engine
    await arm(e, "heat_pump")
    e.clock.set("2026-11-03T06:59:59")
    report(e, "heat_pump")
    await e.c._async_update_data()
    assert e.timer.call_args.args[2] == e.clock.utcnow() + timedelta(seconds=1)
    e.c._pending_command = None
    e.clock.value += timedelta(seconds=1)
    report(e, "heat_pump")
    result = await e.c._async_update_data()
    assert result["reason"] == "fuel_transition"
    assert result["auto_fuel"]["target"] == "gas"
    assert e.service.call_args.args[2]["hvac_mode"] == "off"


async def test_restart_during_handoff_disables_without_replay(engine):
    e = engine
    await arm(e)
    e.clock.value += timedelta(minutes=30)
    report(e)
    await e.c.auto_fuel.step("ultra_low")
    fresh = ClimadoCoordinator(e.hass, e.entry)
    try:
        await fresh.async_restore_runtime()
        assert not fresh.auto_fuel.enabled
        assert "restart" in fresh.auto_fuel.error
        await fresh._async_update_data()
        assert e.service.await_count == 1
    finally:
        await fresh.async_unload()


async def test_restart_when_idle_preserves_enabled_and_restarts_dwell(engine):
    e = engine
    await arm(e)
    fresh = ClimadoCoordinator(e.hass, e.entry)
    try:
        await fresh.async_restore_runtime()
        assert fresh.auto_fuel.enabled
        assert not await fresh.auto_fuel.step("ultra_low")
        e.service.assert_not_called()
    finally:
        await fresh.async_unload()


@pytest.mark.parametrize("interruption", ["manual", "windows", "disabled", "service", "timeout"])
async def test_interruptions_never_start_second_source(engine, interruption):
    e = engine
    await arm(e)
    e.clock.value += timedelta(minutes=30)
    report(e)
    if interruption == "service":
        e.service.side_effect = RuntimeError("connection lost")
    await e.c.auto_fuel.step("ultra_low")
    if interruption == "manual":
        await e.c.async_set_system_mode("off")
    elif interruption == "windows":
        await e.c.windows.async_set_active(True)
    elif interruption == "disabled":
        e.c.enabled = False
    elif interruption == "timeout":
        e.clock.value += timedelta(minutes=20)
        e.c.system.confirm()
    await e.c.auto_fuel.step("ultra_low")
    assert not e.c.auto_fuel.enabled
    assert all(call.args[2].get("hvac_mode") == "off" for call in e.service.await_args_list)


async def test_manual_external_fuel_change_disables_automation(engine):
    await arm(engine)
    report(engine, "heat_pump")
    await engine.c.auto_fuel.step("ultra_low")
    assert not engine.c.auto_fuel.enabled
    engine.service.assert_not_called()


async def test_enable_requires_heat_and_outdoor_and_storage_before_commands(engine, monkeypatch):
    e = engine
    with pytest.raises(ServiceValidationError):
        await e.c.auto_fuel.set_enabled(True)
    report(e)
    with monkeypatch.context() as patch:
        patch.setattr(e.c.auto_fuel._store, "async_save", AsyncMock(side_effect=OSError("disk full")))
        with pytest.raises(OSError):
            await e.c.auto_fuel.set_enabled(True)
    assert not e.c.auto_fuel.enabled
    e.service.assert_not_called()


async def test_manual_hold_and_local_mode_disagreement_block_new_handoffs(engine):
    e = engine
    await arm(e)
    e.clock.value += timedelta(minutes=30)
    report(e)
    e.c._manual_hold = ("temp", 21)
    assert not await e.c.auto_fuel.step("ultra_low")
    e.c._manual_hold = None
    e.entry.options["local_feedback_entity"] = "climate.local"
    e.hass.states.async_set("climate.local", "off", {"hvac_action": "idle"}, timestamp=e.clock.utcnow().timestamp())
    assert not await e.c.auto_fuel.step("ultra_low")
    e.service.assert_not_called()


@pytest.mark.parametrize("state,unit,age", [("6", "°F", 0), ("6", "°C", 15),
                                           ("6", "°C", -1), ("unavailable", "°C", 0)])
async def test_invalid_outdoor_does_not_switch_with_fresh_thermostat(engine, state, unit, age):
    e = engine
    await arm(e)
    e.clock.value += timedelta(minutes=30)
    report(e)
    e.hass.states.async_set("sensor.outdoor", state, {"unit_of_measurement": unit},
                           timestamp=(e.clock.utcnow() - timedelta(minutes=age)).timestamp())
    assert not await e.c.auto_fuel.step("ultra_low")
    assert "outdoor" in e.c.auto_fuel.reason
    e.service.assert_not_called()


async def test_weather_and_tier_are_rechecked_before_restart(engine):
    e = engine
    await arm(e)
    e.clock.value += timedelta(minutes=30)
    report(e)
    await e.c.auto_fuel.step("ultra_low")
    e.clock.value += timedelta(seconds=1)
    report(e, mode="off")
    e.c.system.confirm()
    await e.c.auto_fuel.step("ultra_low")
    e.clock.value += timedelta(minutes=5)
    report(e, mode="off")
    await e.c.auto_fuel.step("mid_peak")
    assert e.c.auto_fuel.target == "gas"
    assert e.service.call_args.args[:2] == ("switch", "turn_on")


async def test_fresh_local_heat_alone_cannot_finish_a_fuel_handoff(engine):
    e = engine
    await arm(e)
    e.clock.value += timedelta(minutes=30)
    report(e)
    await e.c.auto_fuel.step("ultra_low")
    e.clock.value += timedelta(seconds=1)
    report(e, mode="off")
    e.c.system.confirm()
    await e.c.auto_fuel.step("ultra_low")
    e.clock.value += timedelta(minutes=5)
    report(e, mode="off")
    await e.c.auto_fuel.step("ultra_low")
    e.entry.options["local_feedback_entity"] = "climate.local"
    e.clock.value += timedelta(seconds=1)
    report(e, "gas")  # Wrong fuel despite generic Heat matching.
    e.hass.states.async_set("climate.local", "heat", {"hvac_action": "idle"}, timestamp=e.clock.utcnow().timestamp())
    e.c.system.confirm()
    assert await e.c.auto_fuel.step("ultra_low")
    assert e.c.system.pending
    assert e.c.auto_fuel.phase == "starting"
    assert e.service.await_count == 2


async def test_fuel_service_is_scoped_to_one_entry(engine):
    e = engine
    other = AsyncMock()
    e.c.async_set_fuel_automation = AsyncMock()
    e.hass.data["climado"] = {"test": e.c, "other": other}
    _register_services(e.hass)
    await e.real_call(e.hass.services, "climado", "set_fuel_automation",
                      {"entry_id": "test", "enabled": True}, blocking=True)
    e.c.async_set_fuel_automation.assert_awaited_once_with(True)
    other.async_set_fuel_automation.assert_not_called()


async def test_source_change_after_manual_target_adjustment_waits(engine):
    e = engine
    await arm(e)
    e.clock.value += timedelta(minutes=30)
    report(e)
    e.c._last_commanded = ("temp", 21)
    assert not await e.c.auto_fuel.step("ultra_low")
    e.service.assert_not_called()


async def test_stale_aux_requests_bounded_read_refresh_without_hvac_command(engine):
    e = engine
    await arm(e)
    e.clock.value += timedelta(minutes=30)
    report(e)
    e.hass.states.async_set("switch.ecobee_aux", "on",
                           timestamp=(e.clock.utcnow() - timedelta(minutes=11)).timestamp())
    e.c.feedback.request_refresh = Mock()
    assert not await e.c.auto_fuel.step("ultra_low")
    e.c.feedback.request_refresh.assert_called_once()
    e.service.assert_not_called()
