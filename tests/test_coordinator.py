"""Behavior regressions for commands, presence, persistence and deadlines."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.core import Event
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.climado.coordinator import ClimadoCoordinator


def thermostat(engine, **attrs):
    old = engine.hass.states.get("climate.test")
    engine.hass.states.async_set("climate.test", old.state, {**old.attributes, **attrs})


async def test_equal_sleep_and_home_target_exits_preset(engine):
    thermostat(engine, preset_mode="sleep", temperature=23.5)
    state = await engine.c._async_update_data()
    assert state["mode"] == "home"
    assert engine.service.call_args.args[1] == "set_temperature"
    assert engine.hass.states.get("climate.test").attributes["preset_mode"] == "temp"
    engine.clock.value += timedelta(minutes=16)
    state = await engine.c._async_update_data()
    assert state["mode"] == "home"
    assert not state["manual_hold_active"]


async def test_failed_command_retries_without_manual_hold(engine):
    engine.c.tunables["comfort_home"] = 22
    engine.service.side_effect = RuntimeError("Ecobee unavailable")
    state = await engine.c._async_update_data()
    assert state["applied"] is None
    assert state["command_pending"]
    assert state["command_error"] == "Ecobee unavailable"
    assert engine.timer.call_args.args[2] == engine.clock.utcnow() + timedelta(minutes=1)
    await engine.c._async_update_data()
    assert engine.service.call_count == 1
    engine.clock.value += timedelta(minutes=1)
    engine.service.side_effect = engine.echo
    state = await engine.c._async_update_data()
    assert engine.service.call_count == 2
    assert state["applied"] == 22
    assert not state["command_pending"]
    assert not state["manual_hold_active"]


async def test_silent_failure_never_becomes_manual_hold(engine):
    engine.c.tunables["comfort_home"] = 22
    engine.service.side_effect = None  # Service returns, device never confirms.
    await engine.c._async_update_data()
    engine.clock.value += timedelta(minutes=6)
    state = await engine.c._async_update_data()
    assert state["mode"] == "home"
    assert state["command_pending"]
    assert not state["manual_hold_active"]
    assert engine.service.call_count == 2


async def test_delayed_preset_confirmation_reports_correct_target(engine):
    engine.c.set_manual_mode("sleep")
    engine.service.side_effect = None
    state = await engine.c._async_update_data()
    assert state["target"] is None
    assert state["applied"] is None
    assert state["command_pending"]
    await engine.c._async_update_data()
    assert engine.service.call_count == 1
    thermostat(engine, preset_mode="sleep", temperature=23.1, hvac_action="cooling")
    engine.clock.value += timedelta(minutes=3)
    state = await engine.c._async_update_data()
    assert state["target"] == 23.1
    assert state["hvac_action"] == "cooling"
    assert not state["command_pending"]
    assert engine.service.call_count == 1


async def test_real_manual_change_remains_respected(engine):
    await engine.c._async_update_data()
    engine.clock.value += timedelta(minutes=6)
    thermostat(engine, temperature=24.5)
    state = await engine.c._async_update_data()
    assert state["mode"] == "manual_hold"
    assert state["target"] == 24.5
    engine.c.clear_manual_hold()
    state = await engine.c._async_update_data()
    assert state["mode"] == "home"
    assert state["applied"] == 23.5


async def test_manual_hold_expires_at_night_edge(engine):
    await engine.c._async_update_data()
    engine.clock.set("2026-09-07T22:50:00")
    thermostat(engine, temperature=25)
    assert (await engine.c._async_update_data())["mode"] == "manual_hold"
    engine.clock.set("2026-09-07T23:00:00")
    assert (await engine.c._async_update_data())["mode"] == "sleep"


async def test_new_mode_supersedes_pending_command(engine):
    engine.service.side_effect = None
    engine.c.set_manual_mode("sleep")
    await engine.c._async_update_data()
    engine.c.set_manual_mode("away")
    engine.c.clear_manual_hold()
    engine.service.side_effect = engine.echo
    state = await engine.c._async_update_data()
    assert state["mode"] == "away"
    assert state["applied"] == 28
    assert not state["command_pending"]


async def test_manual_sleep_overrides_absence_and_expires(engine):
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    engine.clock.set("2026-09-07T21:00:00")
    engine.c.set_manual_mode("sleep")
    state = await engine.c._async_update_data()
    assert state["mode"] == "sleep"
    assert state["reason"] == "manual_sleep"
    engine.clock.set("2026-09-08T07:00:00")
    state = await engine.c._async_update_data()
    assert state["manual_mode"] == "auto"
    assert state["mode"] == "away"


async def test_manual_home_expires_at_night_start(engine):
    engine.c.set_manual_mode("home")
    await engine.c._async_update_data()
    engine.clock.set("2026-09-07T23:00:00")
    state = await engine.c._async_update_data()
    assert state["manual_mode"] == "auto"
    assert state["mode"] == "sleep"


async def test_away_delay_starts_at_departure_event(engine, monkeypatch):
    await engine.c._async_update_data()
    refresh = AsyncMock()
    monkeypatch.setattr(engine.c, "async_request_refresh", refresh)
    engine.clock.value += timedelta(minutes=14)
    old = engine.hass.states.get("person.test")
    engine.hass.states.async_set("person.test", "not_home")
    engine.c._handle_sensor_event(Event("state_changed", {
        "entity_id": "person.test", "old_state": old,
        "new_state": engine.hass.states.get("person.test"),
    }))
    await engine.hass.async_block_till_done()
    departure = engine.clock.utcnow()
    await engine.c._async_update_data()
    assert engine.timer.call_args.args[2] == departure + timedelta(minutes=45)
    engine.clock.value += timedelta(minutes=31)
    assert (await engine.c._async_update_data())["mode"] == "home"
    engine.clock.value += timedelta(minutes=14)
    assert (await engine.c._async_update_data())["mode"] == "away"


async def test_prearrival_expires_exactly_and_clears_card_state(engine):
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    engine.c.start_prearrival(lead_minutes=5, force=True)
    state = await engine.c._async_update_data()
    assert state["mode"] == "pre_arrival"
    assert engine.timer.call_args.args[2] == engine.clock.utcnow() + timedelta(minutes=5)
    engine.clock.value += timedelta(minutes=5)
    state = await engine.c._async_update_data()
    assert state["mode"] == "home"
    assert state["prearrival_until"] is None
    assert not state["prearrival_active"]


async def test_restart_preserves_away_night_latch(engine):
    engine.clock.set("2026-09-07T20:00:00")
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    engine.clock.set("2026-09-07T23:00:00")
    assert (await engine.c._async_update_data())["mode"] == "away"
    await engine.c.async_unload()
    engine.clock.set("2026-09-08T02:00:00")
    fresh = ClimadoCoordinator(engine.hass, engine.entry)
    await fresh.async_restore_runtime()
    assert (await fresh._async_update_data())["mode"] == "away"
    await fresh.async_unload()


async def test_restart_preserves_arrival_latch_for_same_night(engine):
    engine.clock.set("2026-09-07T20:00:00")
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    engine.clock.set("2026-09-07T23:00:00")
    await engine.c._async_update_data()
    engine.clock.set("2026-09-08T00:00:00")
    engine.hass.states.async_set("person.test", "home")
    await engine.c._async_update_data()
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    await engine.c.async_unload()
    engine.clock.set("2026-09-08T02:00:00")
    fresh = ClimadoCoordinator(engine.hass, engine.entry)
    await fresh.async_restore_runtime()
    state = await fresh._async_update_data()
    assert state["mode"] == "sleep"
    assert not state["night_away_allowed"]
    await fresh.async_unload()


async def test_restart_across_night_boundary_uses_saved_departure(engine):
    engine.clock.set("2026-09-07T20:00:00")
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    await engine.c.async_unload()
    engine.clock.set("2026-09-08T02:00:00")
    fresh = ClimadoCoordinator(engine.hass, engine.entry)
    await fresh.async_restore_runtime()
    assert (await fresh._async_update_data())["mode"] == "away"
    await fresh.async_unload()


async def test_startup_unavailable_presence_preserves_saved_absence(engine):
    engine.clock.set("2026-09-07T20:00:00")
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    await engine.c.async_unload()
    engine.hass.states.async_set("person.test", "unavailable")
    engine.clock.set("2026-09-08T02:00:00")
    fresh = ClimadoCoordinator(engine.hass, engine.entry)
    await fresh.async_restore_runtime()
    assert (await fresh._async_update_data())["mode"] == "away"
    await fresh.async_unload()


async def test_runtime_not_reused_for_different_presence_configuration(engine):
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    await engine.c.async_unload()
    engine.entry.options = {"presence_entities": ["person.other"]}
    fresh = ClimadoCoordinator(engine.hass, engine.entry)
    await fresh.async_restore_runtime()
    assert fresh._absence_since is None
    await fresh.async_unload()


async def test_thermostat_attribute_and_temperature_events_refresh(engine, monkeypatch):
    refresh = AsyncMock()
    monkeypatch.setattr(engine.c, "async_request_refresh", refresh)
    await engine.c.async_setup_listeners()
    thermostat(engine, hvac_action="cooling")
    await engine.hass.async_block_till_done()
    assert refresh.call_count == 1
    engine.hass.states.async_set("sensor.bedroom", "24.8")
    await engine.hass.async_block_till_done()
    assert refresh.call_count == 2
    state = await engine.c._async_update_data()
    assert state["hvac_action"] == "cooling"
    assert state["bedroom_temp"] == 24.8
    # GPS-only attributes must not reschedule the control loop.
    engine.hass.states.async_set("person.test", "home", {"latitude": 43})
    await engine.hass.async_block_till_done()
    assert refresh.call_count == 2


async def test_disable_releases_immediately_and_retries_failure(engine, monkeypatch):
    engine.c.set_manual_mode("sleep")
    engine.service.side_effect = None
    await engine.c._async_update_data()
    engine.c.enabled = False
    monkeypatch.setattr(type(engine.hass.services), "has_service", lambda *a: True)
    engine.service.side_effect = RuntimeError("offline")
    state = await engine.c._async_update_data()
    assert state["mode"] == "disabled"
    assert engine.service.call_args.args[1] == "resume_program"
    assert not engine.c._released
    engine.clock.value += timedelta(minutes=1)
    engine.service.side_effect = engine.echo
    await engine.c._async_update_data()
    assert engine.c._released


async def test_exact_precool_rate_and_workday_boundaries(engine):
    engine.clock.set("2026-09-07T14:29:00")
    state = await engine.c._async_update_data()
    assert state["target"] == 23.5
    assert engine.timer.call_args.args[2] == dt_util.as_utc(engine.clock.value + timedelta(minutes=1))
    engine.clock.set("2026-09-07T14:30:00")
    assert (await engine.c._async_update_data())["target"] == 21.5
    engine.clock.set("2026-09-07T16:00:00")
    assert (await engine.c._async_update_data())["target"] == 25.5
    engine.entry.options["workday_sensor"] = "binary_sensor.workday"
    engine.hass.states.async_set("binary_sensor.workday", "off")
    state = await engine.c._async_update_data()
    assert state["rate_profile"] == "weekend"
    assert state["target"] == 23.5


async def test_service_registry_propagates_device_error(engine, monkeypatch):
    monkeypatch.setattr(type(engine.hass.services), "async_call", engine.real_call)

    async def reject(call):
        raise HomeAssistantError("Device rejected command")

    engine.hass.services.async_register("climate", "set_temperature", reject)
    engine.c.tunables["comfort_home"] = 22
    state = await engine.c._async_update_data()
    assert state["command_error"] == "Device rejected command"
    assert state["applied"] is None
    assert state["command_pending"]


async def test_state_event_updates_coordinator_data_without_extra_writes(engine):
    await engine.c.async_setup_listeners()
    unsubscribe = engine.c.async_add_listener(lambda: None)
    try:
        await engine.c.async_refresh()
        thermostat(engine, hvac_action="cooling", current_temperature=24)
        await engine.hass.async_block_till_done()
        assert engine.c.data["hvac_action"] == "cooling"
        assert engine.c.data["control_temperature"] == 24
        assert engine.service.call_count == 0
    finally:
        unsubscribe()
        await engine.c.async_shutdown()


async def test_restart_preserves_remaining_daytime_away_delay(engine):
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    await engine.c.async_unload()
    engine.clock.value += timedelta(minutes=30)
    fresh = ClimadoCoordinator(engine.hass, engine.entry)
    await fresh.async_restore_runtime()
    assert (await fresh._async_update_data())["mode"] == "home"
    assert engine.timer.call_args.args[2] == engine.clock.utcnow() + timedelta(minutes=15)
    engine.clock.value += timedelta(minutes=15)
    assert (await fresh._async_update_data())["mode"] == "away"
    await fresh.async_unload()


async def test_boundary_bypasses_event_debounce(engine, monkeypatch):
    refresh = AsyncMock()
    request = AsyncMock()
    monkeypatch.setattr(engine.c, "async_refresh", refresh)
    monkeypatch.setattr(engine.c, "async_request_refresh", request)
    engine.c._handle_boundary(engine.clock.utcnow())
    await engine.hass.async_block_till_done()
    refresh.assert_awaited_once()
    request.assert_not_called()


async def test_overlapping_refreshes_do_not_duplicate_commands(engine):
    started = asyncio.Event()
    finish = asyncio.Event()

    async def delayed_echo(*args, **kwargs):
        started.set()
        await finish.wait()
        await engine.echo(*args, **kwargs)

    engine.service.side_effect = delayed_echo
    engine.c.tunables["comfort_home"] = 22
    first = asyncio.create_task(engine.c._async_update_data())
    await started.wait()
    second = asyncio.create_task(engine.c._async_update_data())
    finish.set()
    results = await asyncio.gather(first, second)
    assert engine.service.call_count == 1
    assert all(state["applied"] == 22 for state in results)
