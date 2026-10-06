"""Heating setup is additive and never opts an existing installation in."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.climado import _START_SCHEMA
from custom_components.climado.config_flow import ClimadoOptionsFlow, _structural_schema
from custom_components.climado.const import NUMBER_TUNABLES


def test_schema_defaults_heating_off():
    data = _structural_schema({})({
        "name": "Test", "climate_entity": "climate.test", "main_temp_sensor": "sensor.main",
    })
    assert data["heating_enabled"] is False


@pytest.mark.asyncio
async def test_options_preserve_cooling_and_custom_rates():
    entry = SimpleNamespace(options={
        "comfort_home": 23, "heat_home": 21,
        "rate_plan": {"weekday": [[0, 24, "off_peak"]]},
    })
    flow = ClimadoOptionsFlow(entry)
    flow.async_create_entry = Mock(side_effect=lambda **kwargs: kwargs)
    result = await flow.async_step_init({"heating_enabled": True, "climate_entity": "climate.test"})
    assert result["data"]["comfort_home"] == 23
    assert result["data"]["heat_home"] == 21
    assert result["data"]["rate_plan"] == entry.options["rate_plan"]


def test_heating_and_cooling_number_ids_are_distinct():
    keys = [spec[0] for spec in NUMBER_TUNABLES]
    assert len(keys) == len(set(keys))
    assert {"comfort_home", "away_temp", "vacation_temp", "prearrival_target"} <= set(keys)
    assert {"heat_home", "heat_away", "heat_vacation", "heat_prearrival"} <= set(keys)


def test_prearrival_service_schema_accepts_each_season_threshold():
    assert _START_SCHEMA({"only_if_below": 19})["only_if_below"] == 19
    assert _START_SCHEMA({"only_if_above": 25})["only_if_above"] == 25
    assert _START_SCHEMA({"lead_minutes": 0})["lead_minutes"] == 0
