"""Synthetic inputs exercise the advisory integration, never real equipment."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.climado.advisory import heat_advisory, validate_cost_config
from custom_components.climado.config_flow import ClimadoOptionsFlow
from custom_components.climado.cost_config import cost_schema
from custom_components.climado.sensor import SENSORS


@pytest.fixture
def cost_config():
    return {
        "enabled": True, "prices_consistent": True,
        "electricity_prices": {"ultra_low": .04, "off_peak": .10, "mid_peak": .15, "on_peak": .40},
        "electricity_valid_from": "2026-01-01", "electricity_valid_until": "2026-10-01",
        "electricity_source": "Synthetic test electricity",
        "gas_blocks": [{"upper_m3": 100, "price": .30}, {"price": .25}],
        "gas_valid_from": "2026-07-01", "gas_valid_until": "2026-10-01",
        "gas_source": "Synthetic test gas", "gas_kwh_per_m3": 10,
        "furnace_efficiency": .95, "furnace_aux_kwh_per_heat_kwh": .02,
        "cop_points": [{"outdoor_c": -20, "cop": 1.5}, {"outdoor_c": 20, "cop": 4}],
        "cop_source": "Synthetic curve, not installed equipment", "outdoor_max_age_minutes": 30,
    }


def estimate(e, config, tier="ultra_low"):
    return heat_advisory(e.hass, config, "sensor.outdoor", tier, e.clock.now())


def outdoor(e, value="0", unit="°C"):
    e.hass.states.async_set("sensor.outdoor", value, {"unit_of_measurement": unit}, timestamp=e.clock.utcnow().timestamp())


async def test_coordinator_advises_with_control_disabled_and_changes_at_tier_boundary(engine, cost_config):
    e = engine
    e.c.enabled = False
    e.entry.options.update(outdoor_temp_sensor="sensor.outdoor", heat_cost=cost_config)
    outdoor(e)
    e.clock.set("2026-09-07T06:59:59")
    outdoor(e, "1")
    low = await e.c._async_update_data()
    assert low["heat_advisory"]["preferred_source"] == "heat_pump", low["heat_advisory"]
    e.clock.set("2026-09-07T07:00:00")
    high = await e.c._async_update_data()
    assert high["heat_advisory"]["preferred_source"] == "gas"
    assert high["heat_advisory"]["tier_id"] == "mid_peak"
    e.service.assert_not_called()


async def test_advisory_rejects_stale_then_recovers_on_identical_report(engine, cost_config):
    e = engine
    outdoor(e)
    assert estimate(e, cost_config)["status"] == "ready"
    e.clock.value += timedelta(minutes=30)
    stale = estimate(e, cost_config)
    assert stale["reason"] == "outdoor_stale"
    assert "heat_pump_per_kwh" not in stale
    outdoor(e)  # last_changed is old, last_reported is fresh.
    assert estimate(e, cost_config)["status"] == "ready"


@pytest.mark.parametrize("key,value,reason", [
    ("gas_valid_until", "2026-09-07", "gas_tariff_expired"),
    ("electricity_valid_until", "2026-09-07", "electricity_tariff_expired"),
    ("electricity_valid_from", "2026-09-08", "electricity_tariff_expired"),
])
async def test_tariff_dates_use_local_day_and_exclusive_end(engine, cost_config, key, value, reason):
    outdoor(engine)
    cost_config[key] = value
    assert estimate(engine, cost_config)["reason"] == reason


@pytest.mark.parametrize("value,unit,reason", [("0", "°F", "outdoor_unit"), ("unavailable", "°C", "outdoor_unavailable"), ("-25", "°C", "outside_cop_range")])
async def test_unusable_outdoor_readings_have_no_recommendation(engine, cost_config, value, unit, reason):
    outdoor(engine, value, unit)
    result = estimate(engine, cost_config)
    assert result["reason"] == reason
    assert result["preferred_source"] is None


async def test_unknown_gas_usage_returns_range_and_valid_usage_resolves_block(engine, cost_config):
    outdoor(engine)
    cost_config["gas_usage_sensor"] = "sensor.gas_bill"
    unknown = estimate(engine, cost_config)
    assert not unknown["gas_usage_known"]
    assert unknown["furnace_per_kwh_min"] < unknown["furnace_per_kwh_max"]
    engine.hass.states.async_set("sensor.gas_bill", "150", {"unit_of_measurement": "m³"}, timestamp=engine.clock.utcnow().timestamp())
    known = estimate(engine, cost_config)
    assert known["gas_usage_known"]
    assert known["furnace_per_kwh_min"] == known["furnace_per_kwh_max"]
    engine.hass.states.async_set("sensor.gas_bill", "150", {"unit_of_measurement": "ft³"})
    assert not estimate(engine, cost_config)["gas_usage_known"]


async def test_incomplete_settings_are_explicit_and_do_not_command(engine):
    engine.entry.options["heat_cost"] = {"enabled": True}
    result = (await engine.c._async_update_data())["heat_advisory"]
    assert result["reason"] == "missing_inputs"
    assert "cop_points" in result["missing_inputs"]
    assert "outdoor_temp_sensor" in result["missing_inputs"]
    assert "prices_consistent" in result["missing_inputs"]
    assert all(call.args[1] not in ("set_hvac_mode", "turn_on", "turn_off") for call in engine.service.call_args_list)


def test_native_cost_form_round_trips_complete_data(cost_config):
    result = cost_schema(cost_config)(cost_config)
    assert validate_cost_config(result) == []
    assert result["cop_points"] == cost_config["cop_points"]


@pytest.mark.parametrize("change", [
    {"outdoor_max_age_minutes": "broken"},
    {"outdoor_max_age_minutes": float("inf")},
    {"gas_blocks": ["broken"]},
    {"cop_points": ["broken"]},
    "broken",
])
async def test_corrupt_cost_settings_do_not_interrupt_control_refresh(engine, cost_config, change):
    engine.c.enabled = False
    config = {**cost_config, **change} if isinstance(change, dict) else change
    engine.entry.options.update(outdoor_temp_sensor="sensor.outdoor", heat_cost=config)
    outdoor(engine)
    await engine.c.async_setup_listeners()
    data = await engine.c._async_update_data()
    assert data["heat_advisory"]["reason"] == "invalid_inputs"
    assert data["heat_advisory"]["preferred_source"] is None
    engine.service.assert_not_called()


@pytest.mark.parametrize("change", [
    {"cop_points": [{"outdoor_c": 10, "cop": 3}, {"outdoor_c": 0, "cop": 2}]},
    {"gas_blocks": [{"upper_m3": 100, "price": .3}]},
    {"gas_valid_from": "2026-10-01", "gas_valid_until": "2026-09-01"},
    {"electricity_prices": {"on_peak": .4}}, {"furnace_efficiency": 95},
])
def test_malformed_cost_configuration_is_rejected(cost_config, change):
    with pytest.raises(ValueError):
        validate_cost_config({**cost_config, **change})


async def test_cost_options_preserve_other_settings_and_allow_clear(cost_config):
    entry = SimpleNamespace(data={}, options={"comfort_home": 23, "heat_cost": cost_config, "rate_plan": {"custom": True}})
    flow = ClimadoOptionsFlow(entry)
    flow.async_show_form = Mock(side_effect=lambda **kw: kw)
    flow.async_create_entry = Mock(side_effect=lambda **kw: kw)
    form = await flow.async_step_init({"name": "Test", "configure_heat_cost": True, "heating_enabled": True})
    assert form["step_id"] == "heat_cost"
    result = await flow.async_step_heat_cost({"enabled": True})
    assert result["data"]["comfort_home"] == 23
    assert result["data"]["rate_plan"] == {"custom": True}
    assert "cop_points" not in result["data"]["heat_cost"]
    assert "configure_heat_cost" not in result["data"]


def test_invalid_advisory_clears_numeric_sensors():
    for desc in SENSORS:
        if desc.key in ("heat_pump_cost", "furnace_cost_min", "furnace_cost_max"):
            assert desc.value_fn({"heat_advisory": {"status": "unavailable"}}) is None
