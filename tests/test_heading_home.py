"""Explicit return-home actions must leave away holds and respect HVAC guards."""
from datetime import timedelta

import pytest


async def away_state(e, kind, hvac="cool"):
    e.entry.options["heating_enabled"] = True
    climate = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", hvac, {**climate.attributes, "temperature_unit": "°C"})
    e.hass.states.async_set("person.test", "not_home")
    if kind == "automatic_away":
        e.c._occupied = False
        e.c._absence_since = e.clock.utcnow() - timedelta(hours=1)
    elif kind == "vacation_switch":
        e.c.vacation = True
    else:
        e.c.set_manual_mode(kind)
    await e.c.async_refresh()


@pytest.mark.parametrize("hvac", ["heat", "cool"])
@pytest.mark.parametrize("kind", ["away", "automatic_away", "vacation", "vacation_switch"])
async def test_heading_home_leaves_away_hold_and_applies_seasonal_target(engine, kind, hvac):
    e = engine
    await away_state(e, kind, hvac)
    assert e.c.data["mode"] in ("away", "vacation")
    assert await e.c.async_heading_home()
    await e.c.async_refresh()
    assert e.c.data["mode"] == "pre_arrival"
    assert e.c.data["target"] == (20 if hvac == "heat" else 23)
    assert e.c.manual_mode == "auto"
    assert not e.c.vacation
    assert e.c.data["prearrival_active"]
    calls = e.service.await_count
    assert not await e.c.async_heading_home()  # An active timer cannot be extended by double clicks.
    assert e.service.await_count == calls
    e.clock.value += timedelta(hours=2)
    await e.c.async_refresh()
    assert not e.c.data["prearrival_active"]
    assert e.c.data["mode"] == "away"


@pytest.mark.parametrize("kind", ["home", "sleep", "auto"])
async def test_heading_home_cannot_start_from_home_or_sleep(engine, kind):
    e = engine
    e.c.set_manual_mode(kind)
    await e.c.async_refresh()
    e.service.reset_mock()
    assert not await e.c.async_heading_home()
    assert not e.c._prearrival_active()
    e.service.assert_not_called()


@pytest.mark.parametrize("blocked", ["disabled", "off", "windows", "pending", "fuel_transition", "mode_changed"])
async def test_heading_home_rechecks_guards_before_clearing_away(engine, blocked):
    e = engine
    await away_state(e, "away")
    if blocked == "disabled":
        e.c.enabled = False
    elif blocked == "off":
        e.hass.states.async_set("climate.test", "off")
    elif blocked == "windows":
        e.c.windows.manual = True
    elif blocked == "pending":
        e.c.system.pending = {"mode": "heat"}
    elif blocked == "fuel_transition":
        e.c.auto_fuel.phase = "stopping"
    else:
        e.c.set_manual_mode("home")  # The published away state has not refreshed yet.
    e.service.reset_mock()
    assert not await e.c.async_heading_home()
    assert not e.c._prearrival_active()
    assert e.c.manual_mode == ("home" if blocked == "mode_changed" else "away")
    e.service.assert_not_called()
    e.c.system.pending = None
