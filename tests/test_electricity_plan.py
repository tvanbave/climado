"""Ontario schedules, dated prices, and persisted plan changes."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.climado.rate import default_tou_plan, electricity_price
from custom_components.climado.select import ClimadoElectricityPlanSelect

ZONE = ZoneInfo("America/Toronto")


@pytest.mark.parametrize("date,hour,tier", [
    ("2026-10-21", 6, "off_peak"), ("2026-10-21", 7, "mid_peak"),
    ("2026-10-21", 11, "on_peak"), ("2026-10-21", 17, "mid_peak"),
    ("2026-10-21", 19, "off_peak"), ("2026-11-02", 7, "on_peak"),
    ("2026-11-02", 11, "mid_peak"), ("2026-11-02", 17, "on_peak"),
    ("2026-11-02", 19, "off_peak"), ("2027-04-30", 12, "mid_peak"),
    ("2027-05-03", 12, "on_peak"),
])
def test_tou_summer_winter_boundaries(date, hour, tier):
    now = datetime.fromisoformat(date).replace(hour=hour, tzinfo=ZONE)
    assert default_tou_plan(now).tier_at(now, True).tier_id == tier
    assert default_tou_plan(now).tier_at(now, False).tier_id == "off_peak"


@pytest.mark.parametrize("plan,tier,before,after", [
    ("ULO", "ultra_low", .039, .037), ("ULO", "off_peak", .098, .091),
    ("ULO", "mid_peak", .157, .146), ("ULO", "on_peak", .391, .377),
    ("TOU", "off_peak", .098, .091), ("TOU", "mid_peak", .157, .146),
    ("TOU", "on_peak", .203, .189),
])
def test_november_prices_do_not_apply_early(plan, tier, before, after):
    edge = datetime(2026, 11, 1, tzinfo=ZONE)
    assert electricity_price(plan, tier, edge - timedelta(seconds=1))["per_kwh"] == before
    assert electricity_price(plan, tier, edge)["per_kwh"] == after


def test_unknown_expired_or_incompatible_prices_are_not_invented():
    for plan, tier, year in [("TOU", "ultra_low", 2026), ("invalid", "off_peak", 2026),
                              ("ULO", "mid_peak", 2028), ("ULO", "mid_peak", 2024)]:
        data = electricity_price(plan, tier, datetime(year, 1, 1, tzinfo=ZONE))
        assert data["status"] == "unavailable"
        assert data["per_kwh"] is None


async def test_tou_plan_and_price_remain_visible_with_climado_disabled(engine):
    e = engine
    e.entry.options["electricity_plan"] = "TOU"
    e.c.enabled = False
    e.clock.set("2026-10-21T12:00:00")
    data = await e.c._async_update_data()
    assert data["tier_id"] == "on_peak"
    assert data["electricity_price"]["per_kwh"] == .203
    assert data["electricity_price"]["basis"] == "energy_only"
    assert data["rate_plan"]["weekend"] == [[0, 24, "off_peak"]]
    assert data["heat_advisory"]["reason"] == "rate_plan_mismatch"


async def test_midnight_changes_rates_and_schedules_next_refresh(engine):
    e = engine
    e.c.enabled = False
    e.clock.set("2026-10-31T23:59:59")
    before = await e.c._async_update_data()
    assert before["electricity_price"]["per_kwh"] == .039
    assert e.timer.call_args.args[2] == e.clock.utcnow() + timedelta(seconds=1)
    e.clock.value += timedelta(seconds=1)
    after = await e.c._async_update_data()
    assert after["electricity_price"]["per_kwh"] == .037


async def test_select_persists_without_losing_options_and_disables_ulo_automation(engine, monkeypatch):
    e = engine
    e.entry.options["heat_cost"] = {"enabled": False}
    e.c.auto_fuel.enabled = True
    update = Mock(side_effect=lambda entry, options: setattr(entry, "options", options))
    e.c.async_request_refresh = AsyncMock()
    select = ClimadoElectricityPlanSelect(e.c, e.entry)
    assert select.current_option == "ULO"
    with monkeypatch.context() as patch:
        patch.setattr(e.hass, "config_entries", Mock(async_update_entry=update))
        await select.async_select_option("TOU")
        assert select.current_option == "TOU"
        assert e.entry.options["heat_cost"] == {"enabled": False}
        assert not e.c.auto_fuel.enabled
        assert (await e.c.auto_fuel._store.async_load())["enabled"] is False
        await select.async_select_option("ULO")
    assert not e.c.auto_fuel.enabled  # No implicit reactivation.
    e.service.assert_not_called()


async def test_plan_cannot_change_during_handoff(engine):
    engine.c.auto_fuel.phase = "stopping"
    with pytest.raises(ServiceValidationError, match="finish"):
        await engine.c.async_set_electricity_plan("TOU")
    assert "electricity_plan" not in engine.entry.options


async def test_tou_cannot_enable_ulo_temperature_policy(engine):
    engine.entry.options["electricity_plan"] = "TOU"
    with pytest.raises(ServiceValidationError, match="ULO"):
        await engine.c.auto_fuel.set_enabled(True)
    engine.service.assert_not_called()
