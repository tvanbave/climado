"""Heating is explicit opt-in; these tests never select a fuel source."""
import pytest


def heat(engine, **attrs):
    state = engine.hass.states.get("climate.test")
    engine.hass.states.async_set("climate.test", "heat", {
        **state.attributes, "temperature_unit": "°C", **attrs,
    })


@pytest.mark.asyncio
async def test_heat_requires_opt_in_and_keeps_status(engine):
    heat(engine)
    data = await engine.c._async_update_data()
    assert data["reason"] == "heating_not_enabled"
    assert data["target"] is None
    assert data["hvac_mode"] == "heat"
    assert data["rate_plan"] is not None
    engine.service.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,target", [("home", 20), ("away", 17), ("vacation", 15)])
async def test_separate_heating_targets_preserve_aux(engine, mode, target):
    engine.entry.options["heating_enabled"] = True
    heat(engine, aux_heat="on")
    engine.c.set_manual_mode(mode)
    data = await engine.c._async_update_data()
    assert data["target"] == target
    assert data["applied"] == target
    assert data["fuel_control"] == "manual"
    assert engine.hass.states.get("climate.test").attributes["aux_heat"] == "on"
    assert all(c.args[1] == "set_temperature" for c in engine.service.call_args_list)


@pytest.mark.asyncio
async def test_heat_sleep_and_morning_expiry(engine):
    engine.entry.options["heating_enabled"] = True
    heat(engine)
    engine.clock.set("2026-09-07T23:00:00")
    engine.c.set_manual_mode("sleep")
    data = await engine.c._async_update_data()
    assert data["mode"] == "sleep"
    assert engine.service.call_args.args[1] == "set_preset_mode"
    engine.clock.set("2026-09-08T07:00:00")
    data = await engine.c._async_update_data()
    assert data["mode"] == "home"
    assert data["target"] == 20
    assert engine.c.manual_mode == "auto"


async def test_ecobee_morning_preset_is_not_claimed_as_manual(engine):
    e = engine
    e.entry.options["heating_enabled"] = True
    e.clock.set("2026-10-22T06:00:00")
    heat(e, preset_mode="sleep", temperature=18.5, climate_mode="Sleep")
    assert (await e.c._async_update_data())["mode"] == "sleep"
    e.clock.set("2026-10-22T06:10:00")
    heat(e, temperature=21)  # Editing Sleep's comfort target keeps its preset.
    data = await e.c._async_update_data()
    assert data["mode"] == "sleep"
    assert data["target"] == 21
    e.clock.set("2026-10-22T06:33:00")
    heat(e, preset_mode="home", climate_mode="Home")
    data = await e.c._async_update_data()
    assert data["mode"] == "thermostat_preset"
    assert data["reason"] == "thermostat_preset"
    assert data["target"] == 21
    assert data["applied"] is None
    assert data["manual_hold_value"] == ["preset", "home"]
    e.clock.set("2026-10-22T06:50:00")
    assert (await e.c._async_update_data())["mode"] == "thermostat_preset"
    e.service.assert_not_called()
    e.clock.set("2026-10-22T07:00:00")
    data = await e.c._async_update_data()
    assert data["mode"] == "home"
    assert not data["manual_hold_active"]
    e.service.assert_awaited_once()
    assert e.service.call_args.args[1] == "set_temperature"


@pytest.mark.parametrize("target", [None, "unavailable", float("nan"), float("inf")])
async def test_external_preset_invalid_target_is_not_invented(engine, target):
    e = engine
    e.entry.options["heating_enabled"] = True
    e.clock.set("2026-10-22T06:00:00")
    heat(e, preset_mode="sleep", temperature=21)
    await e.c._async_update_data()
    e.clock.set("2026-10-22T06:33:00")
    heat(e, preset_mode="home", temperature=target)
    data = await e.c._async_update_data()
    assert data["mode"] == "thermostat_preset"
    assert data["target"] is None
    e.service.assert_not_called()


async def test_temperature_adjustment_during_external_preset_is_manual(engine):
    e = engine
    e.entry.options["heating_enabled"] = True
    e.clock.set("2026-10-22T06:00:00")
    heat(e, preset_mode="sleep", temperature=21)
    await e.c._async_update_data()
    e.clock.set("2026-10-22T06:33:00")
    heat(e, preset_mode="home")
    await e.c._async_update_data()
    heat(e, preset_mode="temp", temperature=22)
    data = await e.c._async_update_data()
    assert data["mode"] == "manual_hold"
    assert data["target"] == 22
    e.service.assert_not_called()
    e.c.clear_manual_hold()
    assert (await e.c._async_update_data())["mode"] == "sleep"
    assert e.service.call_args.args[1] == "set_preset_mode"


@pytest.mark.asyncio
async def test_heating_has_no_cooling_rate_offsets(engine):
    engine.entry.options["heating_enabled"] = True
    heat(engine)
    for hour in (15, 17):
        engine.clock.set(f"2026-09-07T{hour}:00:00")
        data = await engine.c._async_update_data()
        assert data["target"] == 20
        assert "Pre-cool" not in str(data["next_transition"])


@pytest.mark.asyncio
async def test_mode_change_cancels_pending_cooling_and_prearrival(engine):
    engine.service.side_effect = RuntimeError("offline")
    engine.c.set_tunable("comfort_home", 22)
    await engine.c._async_update_data()
    assert engine.c._pending_command
    engine.c.start_prearrival(force=True)
    heat(engine)
    data = await engine.c._async_update_data()
    assert not data["command_pending"]
    assert data["target"] is None
    assert not data["prearrival_active"]
    assert data["command_error"] is None
    assert engine.service.call_count == 1


@pytest.mark.asyncio
async def test_heating_prearrival_threshold_and_expiry(engine):
    engine.entry.options["heating_enabled"] = True
    heat(engine)
    engine.hass.states.async_set("person.test", "not_home")
    await engine.c._async_update_data()
    assert not engine.c.start_prearrival()  # main room 23, target 20
    engine.hass.states.async_set("sensor.main", "18", {"unit_of_measurement": "°C"}, timestamp=engine.clock.utcnow().timestamp())
    assert engine.c.start_prearrival(lead_minutes=10, only_if_below=19)
    data = await engine.c._async_update_data()
    assert data["reason"] == "pre_arrival_heat"
    assert data["target"] == 20
    engine.clock.set("2026-09-07T12:10:00")
    data = await engine.c._async_update_data()
    assert not data["prearrival_active"]
    with pytest.raises(ValueError, match="threshold"):
        engine.c.start_prearrival(only_if_above=25)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "heat_cool", "unavailable"])
async def test_unsupported_modes_do_not_write(engine, mode):
    state = engine.hass.states.get("climate.test")
    engine.hass.states.async_set("climate.test", mode, state.attributes)
    engine.c.set_manual_mode("sleep")
    data = await engine.c._async_update_data()
    assert data["mode"] == "inactive"
    assert data["target"] is None
    assert not engine.c.start_prearrival(force=True)
    engine.service.assert_not_called()


@pytest.mark.asyncio
async def test_heat_rejects_fahrenheit_instead_of_writing_celsius_target(engine):
    engine.entry.options["heating_enabled"] = True
    heat(engine, temperature_unit="°F", temperature=70)
    data = await engine.c._async_update_data()
    assert data["reason"] == "unsupported_temperature_unit"
    engine.service.assert_not_called()


@pytest.mark.asyncio
async def test_mode_change_during_command_does_not_confirm_old_target(engine):
    async def change_mode(*args, **kwargs):
        heat(engine)

    engine.service.side_effect = change_mode
    engine.c.set_tunable("comfort_home", 22)
    data = await engine.c._async_update_data()
    assert not data["command_pending"]
    assert engine.c._last_commanded is None
    data = await engine.c._async_update_data()
    assert data["reason"] == "heating_not_enabled"


@pytest.mark.asyncio
async def test_restart_keeps_heating_profile_and_releases_on_disable(engine, monkeypatch):
    from custom_components.climado.coordinator import ClimadoCoordinator

    engine.entry.options.update(heating_enabled=True, heat_home=21)
    heat(engine)
    fresh = ClimadoCoordinator(engine.hass, engine.entry)
    await fresh.async_restore_runtime()
    try:
        data = await fresh._async_update_data()
        assert data["target"] == 21
        monkeypatch.setattr(type(engine.hass.services), "has_service", lambda *args: True)
        fresh.enabled = False
        await fresh._async_update_data()
        assert engine.service.call_args.args[1] == "resume_program"
    finally:
        await fresh.async_unload()


@pytest.mark.asyncio
async def test_aux_selection_change_drops_old_pending_command(engine):
    engine.entry.options["heating_enabled"] = True
    heat(engine, aux_heat="off")
    engine.service.side_effect = RuntimeError("offline")
    await engine.c._async_update_data()
    assert engine.c._pending_command
    heat(engine, aux_heat="on", temperature=20)
    engine.service.reset_mock()
    data = await engine.c._async_update_data()
    assert data["target"] == 20
    assert not data["command_pending"]
    assert not data["manual_hold_active"]
    engine.service.assert_not_called()


@pytest.mark.asyncio
async def test_rounding_stays_in_device_limits(engine):
    engine.entry.options["heating_enabled"] = True
    heat(engine, min_temp=10.2, max_temp=27.8)
    engine.c.set_tunable("heat_home", 30)
    data = await engine.c._async_update_data()
    assert data["applied"] == 27.8


@pytest.mark.asyncio
async def test_disabled_prearrival_does_not_arm(engine):
    engine.c.enabled = False
    assert not engine.c.start_prearrival(force=True)
    assert not engine.c._prearrival_active()


@pytest.mark.asyncio
async def test_heating_opt_out_releases_hold_without_reload_and_retries(engine, monkeypatch):
    from datetime import timedelta

    engine.entry.options["heating_enabled"] = True
    heat(engine)
    await engine.c._async_update_data()
    engine.entry.options["heating_enabled"] = False
    assert not engine.c.structural_changed()
    monkeypatch.setattr(type(engine.hass.services), "has_service", lambda *args: True)
    engine.service.reset_mock()
    engine.service.side_effect = RuntimeError("offline")
    data = await engine.c._async_update_data()
    assert data["reason"] == "heating_not_enabled"
    assert data["command_error"] == "offline"
    assert engine.service.call_args.args[1] == "resume_program"
    await engine.c._async_update_data()
    assert engine.service.call_count == 1
    engine.clock.value += timedelta(minutes=1)
    engine.service.side_effect = engine.echo
    data = await engine.c._async_update_data()
    assert engine.service.call_count == 2
    assert engine.c._released
    assert data["command_error"] is None


@pytest.mark.asyncio
async def test_heat_home_exits_sleep_even_with_equal_target(engine):
    engine.entry.options["heating_enabled"] = True
    heat(engine, temperature=20, preset_mode="sleep")
    engine.c.set_manual_mode("home")
    data = await engine.c._async_update_data()
    assert data["mode"] == "home"
    assert engine.service.call_args.args[1] == "set_temperature"
    engine.clock.set("2026-09-07T23:00:00")
    data = await engine.c._async_update_data()
    assert data["mode"] == "sleep"
    assert engine.c.manual_mode == "auto"


@pytest.mark.parametrize("forced", [False, True])
async def test_climado_sleep_target_and_next_boundary(engine, forced):
    e = engine
    e.entry.options["heating_enabled"] = True
    heat(e, aux_heat="on")
    e.clock.set("2026-09-07T23:30:00" if not forced else "2026-09-07T12:00:00")
    e.c.set_tunable("heat_sleep", 21.5)
    e.c.set_tunable("heat_sleep_enabled", True)
    if forced:
        e.c.set_manual_mode("sleep")
    data = await e.c._async_update_data()
    assert data["mode"] == "sleep"
    assert data["reason"] == "sleep/climado-target"
    assert data["target"] == data["applied"] == 21.5
    assert data["regulating"] == "thermostat"
    assert e.hass.states.get("climate.test").attributes["aux_heat"] == "on"
    assert e.service.call_args.args[1:] == ("set_temperature", {"entity_id": "climate.test", "temperature": 21.5})
    e.service.reset_mock()
    await e.c._async_update_data()
    e.service.assert_not_called()
    e.clock.set("2026-09-08T07:00:00")
    data = await e.c._async_update_data()
    assert data["mode"] == "home"
    assert data["target"] == 20


@pytest.mark.parametrize("cooling,enabled", [(False, False), (True, True)])
async def test_sleep_target_opt_in_never_changes_cooling_or_legacy_sleep(engine, cooling, enabled):
    e = engine
    e.entry.options["heating_enabled"] = True
    if not cooling:
        heat(e)
    e.clock.set("2026-09-07T23:30:00")
    e.c.set_tunable("heat_sleep", 21.5)
    e.c.set_tunable("heat_sleep_enabled", enabled)
    data = await e.c._async_update_data()
    assert data["reason"] == "night/ecobee-sleep"
    assert e.service.call_args.args[1] == "set_preset_mode"


async def test_sleep_edit_replaces_manual_hold_but_inactive_home_edit_does_not(engine, monkeypatch):
    e = engine
    from unittest.mock import AsyncMock

    e.entry.options["heating_enabled"] = True
    heat(e)
    e.clock.set("2026-09-07T23:30:00")
    e.c.set_tunable("heat_sleep_enabled", True)
    e.c.set_tunable("heat_sleep", 21)
    await e.c._async_update_data()
    e.clock.set("2026-09-07T23:40:00")
    heat(e, temperature=22, preset_mode="temp")
    data = await e.c._async_update_data()
    assert data["mode"] == "manual_hold"
    assert data["target"] == 22
    monkeypatch.setattr(e.c, "async_request_refresh", AsyncMock())
    await e.c.async_set_heating_tunable("heat_home", 21.5)
    assert e.c._manual_hold is not None
    await e.c.async_set_heating_tunable("heat_sleep", 21.5)
    data = await e.c._async_update_data()
    assert data["mode"] == "sleep"
    assert not data["manual_hold_active"]
    assert data["target"] == 21.5
    await e.c.async_set_heating_tunable("heat_sleep_enabled", False)
    data = await e.c._async_update_data()
    assert data["reason"] == "night/ecobee-sleep"
    assert e.service.call_args.args[1] == "set_preset_mode"


@pytest.mark.parametrize("block", ["off", "disabled", "heating_disabled", "windows", "vacation", "away"])
async def test_sleep_target_preserves_priority_and_safety(engine, block):
    e = engine
    e.entry.options["heating_enabled"] = block != "heating_disabled"
    heat(e)
    e.clock.set("2026-09-07T23:30:00")
    e.c.set_tunable("heat_sleep_enabled", True)
    e.c.set_tunable("heat_sleep", 21.5)
    if block == "off":
        e.hass.states.async_set("climate.test", "off", e.hass.states.get("climate.test").attributes)
    elif block == "disabled":
        e.c.enabled = False
    elif block == "windows":
        await e.c.async_set_windows_open(True)
        e.service.reset_mock()
    elif block == "vacation":
        e.c.vacation = True
    elif block == "away":
        e.c.set_manual_mode("away")
    data = await e.c._async_update_data()
    assert data["target"] != 21.5
    assert all(call.args[2].get("temperature") != 21.5 for call in e.service.call_args_list)


async def test_sleep_target_waits_for_confirmation_and_reports_failure(engine):
    e = engine
    e.entry.options["heating_enabled"] = True
    heat(e)
    e.clock.set("2026-09-07T23:30:00")
    e.c.set_tunable("heat_sleep_enabled", True)
    e.c.set_tunable("heat_sleep", 21.5)
    e.service.side_effect = RuntimeError("offline")
    data = await e.c._async_update_data()
    assert data["target"] == 21.5
    assert data["applied"] is None
    assert data["command_pending"]
    assert data["command_error"] == "offline"
    await e.c._async_update_data()
    assert e.service.call_count == 1
