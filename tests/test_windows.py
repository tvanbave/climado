"""Window pause uses simulated services only, never real equipment."""
from datetime import timedelta

import pytest

from custom_components.climado.coordinator import ClimadoCoordinator


@pytest.fixture
def window_engine(engine, monkeypatch):
    monkeypatch.setattr(engine.c.windows, "_aux_entity", lambda: "switch.test_aux")
    engine.hass.states.async_set("switch.test_aux", "off")
    old = engine.hass.states.get("climate.test")
    engine.hass.states.async_set("climate.test", old.state, {
        **old.attributes, "fan_mode": "auto", "fan_min_on_time": 45,
    })
    async def echo(domain, service, data, **kwargs):
        if service == "set_hvac_mode":
            old = engine.hass.states.get("climate.test")
            engine.hass.states.async_set("climate.test", data["hvac_mode"], old.attributes)
            engine.hass.states.async_set("switch.test_aux", "off")
        elif domain == "switch":
            assert service == "turn_on"
            old = engine.hass.states.get("climate.test")
            engine.hass.states.async_set("climate.test", "heat", old.attributes)
            engine.hass.states.async_set(data["entity_id"], "on")
        else:
            await engine.echo(domain, service, data, **kwargs)
    engine.service.side_effect = echo
    engine.window_echo = echo
    return engine


async def test_pause_stops_hvac_but_keeps_fan_and_rate_status(window_engine):
    e = window_engine
    await e.c.async_set_windows_open(True)
    assert e.hass.states.get("climate.test").state == "off"
    assert e.c.data["mode"] == "windows_open"
    assert e.c.data["target"] is None
    assert e.c.data["tier"]
    assert e.c.enabled
    attrs = e.hass.states.get("climate.test").attributes
    assert attrs["fan_min_on_time"] == 45
    assert attrs["fan_mode"] == "auto"
    assert e.service.call_count == 1
    await e.c._async_update_data()
    assert e.service.call_count == 1
    assert not e.c.start_prearrival(force=True)


async def test_resume_restores_mode_and_current_target(window_engine):
    e = window_engine
    await e.c.async_set_windows_open(True)
    e.c.set_tunable("comfort_home", 22)
    await e.c.async_set_windows_open(False)
    assert e.hass.states.get("climate.test").state == "cool"
    assert e.c.data["target"] == 22
    assert not e.c.windows.busy


async def test_already_off_stays_off_on_resume(window_engine):
    e = window_engine
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", "off", old.attributes)
    await e.c.async_set_windows_open(True)
    await e.c.async_set_windows_open(False)
    assert e.hass.states.get("climate.test").state == "off"
    e.service.assert_not_called()


async def test_pause_survives_restart_with_original_mode(window_engine):
    e = window_engine
    await e.c.async_set_windows_open(True)
    fresh = ClimadoCoordinator(e.hass, e.entry)
    await fresh.async_restore_runtime()
    try:
        assert fresh.windows.active
        assert fresh.windows.restore["mode"] == "cool"
        data = await fresh._async_update_data()
        assert data["mode"] == "windows_open"
        await fresh.async_set_windows_open(False)
        assert e.hass.states.get("climate.test").state == "cool"
    finally:
        await fresh.async_unload()


async def test_furnace_only_restored_without_aux_off_toggle(window_engine):
    e = window_engine
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", "heat", old.attributes)
    e.hass.states.async_set("switch.test_aux", "on")
    await e.c.async_set_windows_open(True)
    await e.c.async_set_windows_open(False)
    assert e.hass.states.get("switch.test_aux").state == "on"
    assert e.service.call_args.args[:2] == ("switch", "turn_on")
    assert all(call.args[1] in ("set_hvac_mode", "turn_on") for call in e.service.call_args_list)


async def test_failures_wait_and_retry_while_remaining_paused(window_engine):
    e = window_engine
    e.service.side_effect = RuntimeError("offline")
    await e.c.async_set_windows_open(True)
    assert e.c.data["windows_error"] == "offline"
    assert e.c.data["windows_pending"]
    await e.c._async_update_data()
    assert e.service.call_count == 1
    e.clock.value += timedelta(minutes=1)
    e.service.side_effect = e.window_echo
    data = await e.c._async_update_data()
    assert e.hass.states.get("climate.test").state == "off"
    assert data["windows_error"] is None


async def test_unconfirmed_restore_blocks_all_target_writes(window_engine):
    e = window_engine
    await e.c.async_set_windows_open(True)
    e.service.reset_mock()
    e.service.side_effect = None
    await e.c.async_set_windows_open(False)
    assert e.c.data["windows_restoring"]
    assert e.c.data["target"] is None
    await e.c._async_update_data()
    assert e.service.call_count == 1
    assert e.service.call_args.args[1] == "set_hvac_mode"


async def test_disabled_control_does_not_cancel_window_pause(window_engine):
    e = window_engine
    e.c.enabled = False
    await e.c.async_set_windows_open(True)
    e.clock.set("2026-09-07T23:00:00")
    await e.c._async_update_data()
    assert e.c.windows.active
    assert e.hass.states.get("climate.test").state == "off"
    await e.c.async_set_windows_open(False)
    assert not e.c.enabled
    assert e.hass.states.get("climate.test").state == "cool"
    assert all(call.args[1] == "set_hvac_mode" for call in e.service.call_args_list)


async def test_unavailable_thermostat_does_not_lose_original_mode(window_engine):
    e = window_engine
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", "unavailable", old.attributes)
    await e.c.async_set_windows_open(True)
    assert e.c.windows.restore is None
    e.hass.states.async_set("climate.test", "cool", old.attributes)
    await e.c._async_update_data()
    assert e.c.windows.restore["mode"] == "cool"
    assert e.hass.states.get("climate.test").state == "off"


async def test_failed_snapshot_save_never_turns_off_equipment(window_engine, monkeypatch):
    from unittest.mock import AsyncMock

    e = window_engine
    await e.c.windows.async_set_active(True)
    monkeypatch.setattr(e.c.windows._store, "async_save", AsyncMock(side_effect=OSError("disk full")))
    data = await e.c._async_update_data()
    assert data["windows_error"] == "disk full"
    assert e.c.windows.restore is None
    assert e.hass.states.get("climate.test").state == "cool"
    e.service.assert_not_called()


async def test_close_during_pending_off_restores_original_mode(window_engine):
    e = window_engine
    e.service.side_effect = None
    await e.c.async_set_windows_open(True)
    assert e.c.data["windows_pending"]
    # Late off report arrives before the explicit close request is processed.
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", "off", old.attributes)
    e.service.side_effect = e.window_echo
    await e.c.async_set_windows_open(False)
    assert e.hass.states.get("climate.test").state == "cool"
    assert not e.c.windows.busy


async def test_close_before_off_confirmation_waits_then_restores(window_engine):
    e = window_engine
    e.service.side_effect = None
    await e.c.async_set_windows_open(True)
    await e.c.async_set_windows_open(False)
    assert e.c.windows.busy
    assert e.c.data["windows_restoring"]
    assert e.service.call_count == 1
    assert e.c.data["target"] is None
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", "off", old.attributes)
    e.service.side_effect = e.window_echo
    await e.c._async_update_data()
    assert e.hass.states.get("climate.test").state == "cool"
    assert not e.c.windows.busy
