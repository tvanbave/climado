"""Window pause uses simulated services only, never real equipment."""
from datetime import timedelta

import pytest

from custom_components.climado.coordinator import ClimadoCoordinator
from custom_components.climado.window_contacts import WindowContacts
from custom_components.climado.switch import ClimadoWindowsSwitch


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
    assert e.c.data["reason"] == "heating_not_enabled"


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


@pytest.mark.parametrize("gas", [False, True])
async def test_heating_window_pause_spans_sleep_expiry_and_restores_current_target(window_engine, gas):
    e = window_engine
    e.entry.options["heating_enabled"] = True
    e.c._aux_entity = "switch.test_aux"
    e.hass.states.async_set("switch.test_aux", "on" if gas else "off")
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", "heat", {**old.attributes, "temperature_unit": "°C"})
    e.clock.set("2026-09-07T23:00:00")
    e.c.set_manual_mode("sleep")
    await e.c.async_set_windows_open(True)
    e.clock.set("2026-09-08T07:00:00")
    await e.c._async_update_data()
    assert e.c.manual_mode == "auto"
    assert e.hass.states.get("climate.test").state == "off"
    await e.c.async_set_windows_open(False)
    assert e.hass.states.get("climate.test").state == "heat"
    assert e.c.data["target"] == 20
    assert e.c.data["selected_source"] == ("gas" if gas else "heat_pump")
    assert e.hass.states.get("climate.test").attributes["fan_min_on_time"] == 45


CONTACTS = ["binary_sensor.patio", "binary_sensor.window"]


@pytest.fixture
def auto_windows(window_engine):
    e = window_engine
    e.entry.options.update(auto_windows_enabled=True, window_contact_entities=CONTACTS)
    for entity_id in CONTACTS:
        e.hass.states.async_set(entity_id, "off")
    return e


async def contact(e, entity_id, value):
    e.hass.states.async_set(entity_id, value)
    # Exercise the edge handler separately from the debounced refresh.
    e.c.window_contacts.observe(entity_id, value)
    return await e.c._async_update_data()


async def test_auto_pause_exact_open_and_close_boundaries(auto_windows):
    e = auto_windows
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    assert e.c.window_contacts.deadline == e.clock.utcnow() + timedelta(minutes=5)
    assert e.timer.call_args.args[2] == e.c.window_contacts.deadline
    e.clock.value += timedelta(minutes=5, microseconds=-1)
    assert (await e.c._async_update_data())["auto_windows"]["status"] == "waiting_to_pause"
    assert not e.c.windows.active
    e.clock.value += timedelta(microseconds=1)
    data = await e.c._async_update_data()
    assert data["windows_automatic"]
    assert data["mode"] == "windows_open"
    assert e.hass.states.get("climate.test").state == "off"
    await contact(e, CONTACTS[0], "off")
    assert e.c.window_contacts.deadline == e.clock.utcnow() + timedelta(minutes=2)
    # Cold-pause monitoring can wake earlier, but never later than closure.
    assert e.timer.call_args.args[2] <= e.c.window_contacts.deadline
    e.clock.value += timedelta(minutes=2, microseconds=-1)
    assert (await e.c._async_update_data())["auto_windows"]["status"] == "waiting_to_resume"
    e.clock.value += timedelta(microseconds=1)
    assert not (await e.c._async_update_data())["windows_open"]
    assert e.hass.states.get("climate.test").state == "cool"
    assert e.hass.states.get("climate.test").attributes["fan_min_on_time"] == 45


async def test_brief_patio_use_and_other_doors_do_not_pause(auto_windows):
    e = auto_windows
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    e.clock.value += timedelta(minutes=1)
    await contact(e, CONTACTS[0], "off")
    e.hass.states.async_set("binary_sensor.front_door", "on")
    e.clock.value += timedelta(minutes=20)
    await e.c._async_update_data()
    assert not e.c.windows.active
    assert not any(c.args[1] == "set_hvac_mode" for c in e.service.call_args_list)


async def test_brief_closure_resets_open_delay_even_between_refreshes(auto_windows):
    from homeassistant.core import Event
    from unittest.mock import AsyncMock, patch

    e = auto_windows
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    e.clock.value += timedelta(minutes=4)
    with patch.object(e.c, "async_request_refresh", new=AsyncMock()):
        for value in ("off", "on"):
            e.hass.states.async_set(CONTACTS[0], value)
            e.c._handle_window_event(Event("state_changed", {
                "entity_id": CONTACTS[0], "new_state": e.hass.states.get(CONTACTS[0]),
            }))
        await e.hass.async_block_till_done()
    e.clock.value += timedelta(minutes=1)
    await e.c._async_update_data()
    assert not e.c.windows.active
    assert e.c.window_contacts.deadline == e.clock.utcnow() + timedelta(minutes=4)


async def test_different_open_contacts_cannot_accumulate_open_time(auto_windows):
    e = auto_windows
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    e.clock.value += timedelta(minutes=4)
    await contact(e, CONTACTS[1], "on")
    await contact(e, CONTACTS[0], "off")
    e.clock.value += timedelta(minutes=1)
    await e.c._async_update_data()
    assert not e.c.windows.active
    e.clock.value += timedelta(minutes=4)
    assert (await e.c._async_update_data())["windows_automatic"]


async def pause_automatically(e):
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    e.clock.value += timedelta(minutes=5)
    await e.c._async_update_data()
    assert e.c.windows.automatic


async def test_manual_pause_is_not_cleared_by_contacts(auto_windows):
    e = auto_windows
    await e.c.async_set_windows_open(True)
    await pause_automatically(e)
    await contact(e, CONTACTS[0], "off")
    e.clock.value += timedelta(minutes=2)
    await e.c._async_update_data()
    assert e.c.windows.manual
    assert not e.c.windows.automatic
    assert e.hass.states.get("climate.test").state == "off"
    await e.c.async_set_windows_open(False)
    assert e.hass.states.get("climate.test").state == "cool"


async def test_manual_switch_cannot_clear_automatic_pause(auto_windows):
    e = auto_windows
    await pause_automatically(e)
    switch = ClimadoWindowsSwitch(e.c, e.entry)
    assert not switch.is_on
    assert switch.extra_state_attributes["automatic_pause"]
    await e.c.async_set_windows_open(True)
    await e.c.async_set_windows_open(False)
    assert e.c.windows.automatic
    assert e.hass.states.get("climate.test").state == "off"


@pytest.mark.parametrize("missing", ["unknown", "unavailable", None])
async def test_unknown_contact_never_counts_as_closed(auto_windows, missing):
    e = auto_windows
    await pause_automatically(e)
    await contact(e, CONTACTS[0], "off")
    if missing is None:
        e.hass.states.async_remove(CONTACTS[1])
    else:
        e.hass.states.async_set(CONTACTS[1], missing)
    await e.c._async_update_data()
    e.clock.value += timedelta(minutes=5)
    data = await e.c._async_update_data()
    assert data["auto_windows"]["status"] == "sensor_unavailable"
    assert e.c.windows.automatic
    await contact(e, CONTACTS[1], "off")
    assert e.c.window_contacts.deadline == e.clock.utcnow() + timedelta(minutes=2)


async def test_reopening_cancels_resume_countdown(auto_windows):
    e = auto_windows
    await pause_automatically(e)
    await contact(e, CONTACTS[0], "off")
    e.clock.value += timedelta(minutes=1)
    await contact(e, CONTACTS[1], "on")
    e.clock.value += timedelta(minutes=2)
    await e.c._async_update_data()
    assert e.c.windows.automatic
    assert e.c.window_contacts.deadline is None


async def test_auto_restart_preserves_ownership_and_rechecks_closed_delay(auto_windows):
    e = auto_windows
    await pause_automatically(e)
    await contact(e, CONTACTS[0], "off")
    e.clock.value += timedelta(minutes=1)
    fresh = ClimadoCoordinator(e.hass, e.entry)
    await fresh.async_restore_runtime()
    try:
        assert fresh.windows.automatic and not fresh.windows.manual
        assert fresh.windows.restore["mode"] == "cool"
        await fresh._async_update_data()
        assert fresh.window_contacts.deadline == e.clock.utcnow() + timedelta(minutes=2)
        e.clock.value += timedelta(minutes=2)
        await fresh._async_update_data()
        assert not fresh.windows.busy
        assert e.hass.states.get("climate.test").state == "cool"
    finally:
        await fresh.async_unload()


@pytest.mark.parametrize("setting,value", [
    ("auto_windows_enabled", False), ("window_contact_entities", []),
])
async def test_disabling_monitoring_preserves_pause_for_manual_release(auto_windows, setting, value):
    e = auto_windows
    await pause_automatically(e)
    e.entry.options[setting] = value
    await e.c._async_update_data()
    assert e.c.windows.manual and not e.c.windows.automatic
    assert e.hass.states.get("climate.test").state == "off"
    await e.c.async_set_windows_open(False)
    assert e.hass.states.get("climate.test").state == "cool"


@pytest.mark.parametrize("mode,gas", [("off", False), ("heat", False), ("heat", True)])
async def test_auto_restore_preserves_off_or_heating_source(auto_windows, mode, gas):
    e = auto_windows
    e.entry.options["heating_enabled"] = True
    e.c._aux_entity = "switch.test_aux"
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", mode, {**old.attributes, "temperature_unit": "°C"})
    e.hass.states.async_set("switch.test_aux", "on" if gas else "off")
    await pause_automatically(e)
    await contact(e, CONTACTS[0], "off")
    e.clock.value += timedelta(minutes=2)
    await e.c._async_update_data()
    assert e.hass.states.get("climate.test").state == mode
    assert (e.hass.states.get("switch.test_aux").state == "on") == gas
    assert e.hass.states.get("climate.test").attributes["fan_min_on_time"] == 45


async def test_legacy_pause_storage_migrates_to_manual(window_engine):
    e = window_engine
    await e.c.windows._store.async_save({"climate_entity": "climate.test", "active": True, "restore": None})
    await e.c.windows.async_load()
    assert e.c.windows.manual and not e.c.windows.automatic


async def test_unknown_interrupts_open_qualification(auto_windows):
    e = auto_windows
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    e.clock.value += timedelta(minutes=4)
    await contact(e, CONTACTS[0], "unavailable")
    await contact(e, CONTACTS[0], "on")
    e.clock.value += timedelta(minutes=1)
    await e.c._async_update_data()
    assert not e.c.windows.active


async def test_restart_does_not_count_unobserved_open_time(auto_windows):
    e = auto_windows
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    e.clock.value += timedelta(minutes=30)
    e.c.window_contacts = WindowContacts()
    await e.c._async_update_data()
    assert not e.c.windows.active
    assert e.c.window_contacts.deadline == e.clock.utcnow() + timedelta(minutes=5)


async def test_auto_pause_retries_failed_off_command(auto_windows):
    e = auto_windows
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    e.service.side_effect = RuntimeError("offline")
    e.clock.value += timedelta(minutes=5)
    data = await e.c._async_update_data()
    assert data["windows_automatic"] and data["windows_error"] == "offline"
    e.service.reset_mock()
    await e.c._async_update_data()
    e.service.assert_not_called()
    e.clock.value += timedelta(minutes=1)
    e.service.side_effect = e.window_echo
    await e.c._async_update_data()
    assert e.hass.states.get("climate.test").state == "off"


async def test_auto_ownership_storage_failure_does_not_turn_off(auto_windows, monkeypatch):
    from unittest.mock import AsyncMock

    e = auto_windows
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    e.clock.value += timedelta(minutes=5)
    e.service.reset_mock()
    monkeypatch.setattr(e.c.windows._store, "async_save", AsyncMock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        await e.c._async_update_data()
    assert not e.c.windows.active
    e.service.assert_not_called()


async def test_auto_close_waits_for_off_confirmation(auto_windows):
    e = auto_windows
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    e.service.side_effect = None
    e.service.reset_mock()
    e.clock.value += timedelta(minutes=5)
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "off")
    e.clock.value += timedelta(minutes=2)
    data = await e.c._async_update_data()
    assert data["windows_restoring"]
    assert e.c.windows.restore["mode"] == "cool"
    assert e.service.call_count == 1
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", "off", old.attributes)
    e.service.side_effect = e.window_echo
    await e.c._async_update_data()
    assert not e.c.windows.busy
    assert e.hass.states.get("climate.test").state == "cool"


async def test_contact_listener_is_registered_and_removed(auto_windows):
    from unittest.mock import Mock, patch, AsyncMock
    from homeassistant.core import Event

    e = auto_windows
    unsubscribe = Mock()
    with patch("custom_components.climado.coordinator.async_track_state_change_event", return_value=unsubscribe) as track:
        await e.c.async_setup_listeners()
    assert any(call.args[1] == CONTACTS and call.args[2] == e.c._handle_window_event
               for call in track.call_args_list)
    with patch.object(e.c, "async_request_refresh", new=AsyncMock()):
        e.c._handle_window_event(Event("state_changed", {
            "entity_id": CONTACTS[0], "new_state": None,
        }))
        await e.hass.async_block_till_done()
    assert e.c.window_contacts.states[CONTACTS[0]] == "unknown"
    await e.c.async_unload()
    assert unsubscribe.called


async def test_window_delays_are_tunable(auto_windows):
    e = auto_windows
    e.c.set_tunable("window_open_delay_minutes", 3)
    e.c.set_tunable("window_close_delay_minutes", 1)
    await e.c._async_update_data()
    await contact(e, CONTACTS[0], "on")
    assert e.c.window_contacts.deadline == e.clock.utcnow() + timedelta(minutes=3)
    e.clock.value += timedelta(minutes=3)
    await e.c._async_update_data()
    assert e.c.windows.automatic
    await contact(e, CONTACTS[0], "off")
    assert e.c.window_contacts.deadline == e.clock.utcnow() + timedelta(minutes=1)
