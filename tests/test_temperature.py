"""Indoor freshness must not change native thermostat or fuel control."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.climado.temperature import temperature_reading


def report(e, value="23", unit="°C", age=timedelta(0)):
    e.hass.states.async_set("sensor.main", value, {"unit_of_measurement": unit},
                           timestamp=(e.clock.utcnow() - age).timestamp())


@pytest.mark.parametrize("value,unit,age,status", [
    ("23", "°C", timedelta(minutes=14, seconds=59), "ready"),
    ("23", "°C", timedelta(minutes=15), "stale"),
    ("23", "°C", timedelta(seconds=-1), "future_report"),
    ("nan", "°C", timedelta(0), "invalid"),
    ("inf", "°C", timedelta(0), "invalid"),
    ("bad", "°C", timedelta(0), "invalid"),
    ("unavailable", "°C", timedelta(0), "unavailable"),
    ("unknown", "°C", timedelta(0), "unavailable"),
    ("73", "°F", timedelta(0), "unsupported_unit"),
    ("23", None, timedelta(0), "unsupported_unit"),
])
async def test_indoor_observation_validation(engine, value, unit, age, status):
    report(engine, value, unit, age)
    result = temperature_reading(engine.hass, "sensor.main")
    assert result["status"] == status
    assert result["value"] == (23 if status == "ready" else None)
    assert engine.c.alerts._temperature("sensor.main") == result["value"]


async def test_missing_or_unconfigured_reading(engine):
    assert temperature_reading(engine.hass, None)["status"] == "not_configured"
    assert temperature_reading(engine.hass, "sensor.missing")["status"] == "unavailable"


async def test_expiry_hides_old_value_without_stopping_native_control(engine):
    e = engine
    state = await e.c._async_update_data()
    assert state["main_temp"] == 23
    assert e.timer.call_args.args[2] == e.clock.utcnow() + timedelta(minutes=15)
    e.clock.value += timedelta(minutes=15)
    state = await e.c._async_update_data()
    assert state["main_temp"] is None
    assert state["room_readings"]["main"]["status"] == "stale"
    assert state["mode"] == "home"
    assert state["target"] == 23.5
    e.service.assert_not_called()


async def test_identical_reports_recover_and_renew_expiry(engine, monkeypatch):
    e = engine
    refresh = AsyncMock()
    monkeypatch.setattr(e.c, "async_request_refresh", refresh)
    await e.c.async_setup_listeners()
    e.clock.value += timedelta(minutes=16)
    assert (await e.c._async_update_data())["main_temp"] is None
    report(e)
    await e.hass.async_block_till_done()
    refresh.assert_awaited_once()
    state = await e.c._async_update_data()
    assert state["main_temp"] == 23
    assert state["room_readings"]["main"]["status"] == "ready"
    assert e.timer.call_args.args[2] == e.clock.utcnow() + timedelta(minutes=15)
    await e.c.async_unload()
    refresh.reset_mock()
    report(e, "24")
    await e.hass.async_block_till_done()
    refresh.assert_not_called()


async def test_unit_only_change_refreshes_and_invalidates(engine, monkeypatch):
    refresh = AsyncMock()
    monkeypatch.setattr(engine.c, "async_request_refresh", refresh)
    await engine.c.async_setup_listeners()
    report(engine, unit="°F")
    await engine.hass.async_block_till_done()
    refresh.assert_awaited_once()
    assert (await engine.c._async_update_data())["main_temp"] is None


@pytest.mark.parametrize("heating", [False, True])
async def test_conditional_prearrival_rejects_stale_but_explicit_button_remains_available(engine, heating):
    e = engine
    if heating:
        e.entry.options["heating_enabled"] = True
        e.hass.states.async_set("climate.test", "heat", {"temperature": 21})
    report(e, "18" if heating else "28", age=timedelta(minutes=15))
    assert not e.c.start_prearrival()
    report(e, "18" if heating else "28")
    assert e.c.start_prearrival()
    e.c.clear_prearrival()
    e.clock.value += timedelta(minutes=15)
    assert e.c.start_prearrival(force=True)
    e.service.assert_not_called()
