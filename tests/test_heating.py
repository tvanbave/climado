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
    engine.hass.states.async_set("sensor.main", "18")
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
