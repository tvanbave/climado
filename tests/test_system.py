"""Manual mode commands against simulated Ecobee state and services only."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError

from custom_components.climado import _register_services
from custom_components.climado.coordinator import ClimadoCoordinator


def setup_system(e, mode="off", equipment="", action="idle", aux="off"):
    e.c._aux_entity = "switch.ecobee_aux"
    e.entry.options["aux_heat_switch"] = "switch.ecobee_aux"
    attrs = dict(e.hass.states.get("climate.test").attributes)
    attrs.update(hvac_modes=["off", "cool", "heat", "heat_cool"], equipment_running=equipment,
                 hvac_action=action, fan_min_on_time=45)
    e.hass.states.async_set("climate.test", mode, attrs, timestamp=e.clock.utcnow().timestamp())
    e.hass.states.async_set("switch.ecobee_aux", aux, timestamp=e.clock.utcnow().timestamp())
    e.hass.services.async_register("climate", "set_hvac_mode", AsyncMock())
    e.hass.services.async_register("switch", "turn_on", AsyncMock())


@pytest.mark.parametrize("mode,source,domain,service,data", [
    ("heat", "gas", "switch", "turn_on", {"entity_id": "switch.ecobee_aux"}),
    ("heat", "heat_pump", "climate", "set_hvac_mode", {"entity_id": "climate.test", "hvac_mode": "heat"}),
    ("cool", None, "climate", "set_hvac_mode", {"entity_id": "climate.test", "hvac_mode": "cool"}),
])
async def test_explicit_mode_command_and_confirmation_guard(engine, mode, source, domain, service, data):
    e = engine
    setup_system(e, equipment="fan", action="fan")
    context = Context()
    await e.c.async_set_system_mode(mode, source, context)
    e.service.assert_awaited_once_with(domain, service, data, blocking=True, context=context)
    assert e.c.data["system_control"]["pending"]["mode"] == mode
    assert e.c.data["reason"] == "system_mode_pending"
    assert e.c.data["system_control"]["hvac_mode"] == "off"
    assert e.hass.states.get("climate.test").attributes["fan_min_on_time"] == 45
    await e.c._async_update_data()
    assert e.service.await_count == 1  # No setpoints or automatic retries.


async def test_confirmed_heat_mode_resumes_heating_profiles(engine):
    e = engine
    setup_system(e)
    e.entry.options["heating_enabled"] = True
    await e.c.async_set_system_mode("heat", "gas")
    e.clock.value += timedelta(seconds=1)
    setup_system(e, mode="heat", aux="on")
    result = await e.c._async_update_data()
    assert result["system_control"]["pending"] is None
    assert result["system_control"]["heat_source"] == "gas"
    assert result["target"] == 20
    assert e.service.call_args.args[1] == "set_temperature"


@pytest.mark.parametrize("mode,equipment,action", [
    ("heat", "", "idle"), ("cool", "", "idle"),
    ("off", "heatPump", "heating"), ("off", "auxHeat1", "heating"),
    ("off", None, "idle"), ("off", "fan", "heating"),
])
async def test_active_or_unknown_equipment_cannot_start_or_change_fuel(engine, mode, equipment, action):
    setup_system(engine, mode=mode, equipment=equipment, action=action)
    with pytest.raises(ServiceValidationError):
        await engine.c.system.async_set("heat", "gas")
    engine.service.assert_not_called()


async def test_off_can_cancel_pending_or_running_equipment(engine):
    e = engine
    setup_system(e)
    await e.c.async_set_system_mode("heat", "gas")
    await e.c.async_set_system_mode("off")
    assert e.service.call_args.args[:2] == ("climate", "set_hvac_mode")
    assert e.service.call_args.args[2]["hvac_mode"] == "off"
    setup_system(e, mode="heat", equipment="heatPump", action="heating")
    await e.c.async_set_system_mode("off")
    assert e.service.call_args.args[2]["hvac_mode"] == "off"


@pytest.mark.parametrize("mode,source", [("heat_cool", None), ("heat", "auto"), ("heat", None), ("cool", "gas")])
async def test_unsupported_requests_are_rejected(engine, mode, source):
    setup_system(engine)
    with pytest.raises(ServiceValidationError):
        await engine.c.system.async_set(mode, source)
    engine.service.assert_not_called()


@pytest.mark.parametrize("active", [True, False])
async def test_windows_pause_and_restoration_block_manual_modes(engine, active):
    setup_system(engine)
    engine.c.windows.active = active
    engine.c.windows.restore = {"mode": "heat", "phase": "paused"}
    with pytest.raises(ServiceValidationError, match="Windows"):
        await engine.c.system.async_set("heat", "gas")
    engine.service.assert_not_called()


async def test_stale_and_unavailable_states_are_not_used_to_start_heat(engine):
    e = engine
    setup_system(e)
    e.clock.value += timedelta(minutes=10)
    with pytest.raises(ServiceValidationError, match="stale"):
        await e.c.system.async_set("heat", "gas")
    setup_system(e, aux="unavailable")
    with pytest.raises(ServiceValidationError, match="Auxiliary"):
        await e.c.system.async_set("heat", "heat_pump")
    e.service.assert_not_called()


async def test_pending_timeout_clears_guard_without_repeating_command(engine):
    setup_system(engine)
    await engine.c.async_set_system_mode("heat", "gas")
    engine.clock.value += timedelta(minutes=5)
    result = await engine.c._async_update_data()
    assert result["system_control"]["pending"] is None
    assert "did not confirm" in result["system_control"]["error"]
    assert engine.service.await_count == 1


async def test_service_failure_preserves_uncertain_delivery_guard(engine):
    setup_system(engine)
    engine.service.side_effect = RuntimeError("Request timed out")
    with pytest.raises(RuntimeError, match="timed out"):
        await engine.c.async_set_system_mode("heat", "gas")
    result = await engine.c._async_update_data()
    assert result["system_control"]["pending"]
    assert result["system_control"]["error"] == "Request timed out"
    assert engine.service.await_count == 1


async def test_off_cancellation_waits_for_a_new_report_not_cached_off(engine):
    setup_system(engine)
    engine.service.side_effect = None
    await engine.c.async_set_system_mode("heat", "gas")
    await engine.c.async_set_system_mode("off")
    assert engine.c.system.pending["mode"] == "off"
    engine.clock.value += timedelta(seconds=1)
    setup_system(engine)
    engine.c.system.confirm()
    assert engine.c.system.pending is None


async def test_system_service_targets_only_requested_entry(engine):
    e = engine
    e.c.async_set_system_mode = AsyncMock()
    other = AsyncMock()
    e.hass.data["climado"] = {"test": e.c, "other": other}
    _register_services(e.hass)
    context = Context()
    await e.real_call(e.hass.services, "climado", "set_system_mode", {"entry_id": "test", "hvac_mode": "heat", "heat_source": "gas"}, blocking=True, context=context)
    e.c.async_set_system_mode.assert_awaited_once_with("heat", "gas", context)
    other.async_set_system_mode.assert_not_called()
    with pytest.raises(ServiceValidationError, match="not loaded"):
        await e.real_call(e.hass.services, "climado", "set_system_mode", {"entry_id": "missing", "hvac_mode": "off"}, blocking=True)


async def test_pending_mode_survives_restart_without_replaying(engine):
    e = engine
    setup_system(e)
    e.service.side_effect = None
    await e.c.async_set_system_mode("heat", "gas")
    fresh = ClimadoCoordinator(e.hass, e.entry)
    try:
        await fresh.async_restore_runtime()
        data = await fresh._async_update_data()
        assert data["reason"] == "system_mode_pending"
        assert fresh.system.pending["source"] == "gas"
        assert e.service.await_count == 1
        e.clock.value += timedelta(seconds=1)
        setup_system(e, mode="heat", aux="on")
        data = await fresh._async_update_data()
        assert data["system_control"]["pending"] is None
        assert e.service.await_count == 1
    finally:
        await fresh.async_unload()


async def test_expired_pending_request_after_restart_reports_failure_without_replay(engine):
    setup_system(engine)
    engine.service.side_effect = None
    await engine.c.async_set_system_mode("heat", "gas")
    engine.clock.value += timedelta(minutes=6)
    fresh = ClimadoCoordinator(engine.hass, engine.entry)
    try:
        await fresh.async_restore_runtime()
        data = await fresh._async_update_data()
        assert data["system_control"]["pending"] is None
        assert "did not confirm" in data["system_control"]["error"]
        assert data["heating_alerts"]["status"] == "command_failed"
        assert engine.service.await_count == 1
    finally:
        await fresh.async_unload()


async def test_storage_failure_prevents_mode_command(engine, monkeypatch):
    setup_system(engine)
    with monkeypatch.context() as patch:
        patch.setattr(engine.c.system._store, "async_save", AsyncMock(side_effect=OSError("disk full")))
        with pytest.raises(OSError):
            await engine.c.system.async_set("heat", "gas")
    engine.service.assert_not_called()
    assert engine.c.system.pending is None


async def test_windows_pause_cancels_persisted_manual_request(engine):
    setup_system(engine)
    engine.service.side_effect = None
    await engine.c.async_set_system_mode("heat", "gas")
    await engine.c.async_set_windows_open(True)
    assert engine.c.system.pending is None
    saved = await engine.c.system._store.async_load()
    assert saved["pending"] is None


async def test_failed_off_request_save_keeps_previous_pending_guard(engine, monkeypatch):
    setup_system(engine)
    engine.service.side_effect = None
    await engine.c.async_set_system_mode("heat", "gas")
    with monkeypatch.context() as patch:
        patch.setattr(engine.c.system._store, "async_save", AsyncMock(side_effect=OSError("disk full")))
        with pytest.raises(OSError):
            await engine.c.system.async_set("off")
    assert engine.c.system.pending["source"] == "gas"
    assert engine.service.await_count == 1
