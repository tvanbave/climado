"""Heating setup is additive and never opts an existing installation in."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.climado import _START_SCHEMA
from custom_components.climado.config_flow import ClimadoOptionsFlow, _structural_schema
from custom_components.climado.const import NUMBER_TUNABLES


def test_schema_defaults_heating_off():
    data = _structural_schema({})({
        "name": "Test", "climate_entity": "climate.test", "main_temp_sensor": "sensor.main",
    })
    assert data["heating_enabled"] is False
    assert data["auto_windows_enabled"] is False
    assert data["window_contact_entities"] == []


@pytest.mark.asyncio
async def test_windows_options_can_clear_initial_contacts_and_preserve_timing():
    entry = SimpleNamespace(
        data={"window_contact_entities": ["binary_sensor.patio"], "auto_windows_enabled": True},
        options={"window_open_delay_minutes": 3, "window_close_delay_minutes": 2},
    )
    flow = ClimadoOptionsFlow(entry)
    flow.async_create_entry = Mock(side_effect=lambda **kwargs: kwargs)
    result = await flow.async_step_init({"climate_entity": "climate.test"})
    assert result["data"]["window_contact_entities"] == []
    assert result["data"]["auto_windows_enabled"] is False
    assert result["data"]["window_open_delay_minutes"] == 3
    assert result["data"]["window_close_delay_minutes"] == 2


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
    assert {"heat_home", "heat_sleep", "heat_away", "heat_vacation", "heat_prearrival"} <= set(keys)


def test_prearrival_service_schema_accepts_each_season_threshold():
    assert _START_SCHEMA({"only_if_below": 19})["only_if_below"] == 19
    assert _START_SCHEMA({"only_if_above": 25})["only_if_above"] == 25
    assert _START_SCHEMA({"lead_minutes": 0})["lead_minutes"] == 0


@pytest.mark.parametrize("saved,expected", [(None, False), ("off", False), ("on", True)])
async def test_sleep_switch_restores_only_explicit_opt_in(engine, monkeypatch, saved, expected):
    from homeassistant.helpers.update_coordinator import CoordinatorEntity
    from custom_components.climado.switch import ClimadoSleepHeatingSwitch

    monkeypatch.setattr(CoordinatorEntity, "async_added_to_hass", AsyncMock())
    switch = ClimadoSleepHeatingSwitch(engine.c, engine.entry)
    monkeypatch.setattr(switch, "async_get_last_state", AsyncMock(
        return_value=SimpleNamespace(state=saved) if saved else None))
    await switch.async_added_to_hass()
    assert switch.is_on is expected
    assert switch.extra_state_attributes["climado_key"] == "heat_sleep_enabled"
    engine.service.assert_not_called()


async def test_sleep_number_restore_and_edit(engine, monkeypatch):
    from homeassistant.helpers.update_coordinator import CoordinatorEntity
    from custom_components.climado.number import ClimadoNumber

    monkeypatch.setattr(CoordinatorEntity, "async_added_to_hass", AsyncMock())
    spec = next(spec for spec in NUMBER_TUNABLES if spec[0] == "heat_sleep")
    number = ClimadoNumber(engine.c, engine.entry, *spec)
    monkeypatch.setattr(number, "async_get_last_number_data", AsyncMock(
        return_value=SimpleNamespace(native_value=21.5)))
    monkeypatch.setattr(number, "async_write_ha_state", Mock())
    monkeypatch.setattr(engine.c, "async_request_refresh", AsyncMock())
    await number.async_added_to_hass()
    assert number.native_value == engine.c.tune("heat_sleep") == 21.5
    await number.async_set_native_value(22)
    assert number.native_value == engine.c.tune("heat_sleep") == 22
    engine.c.async_request_refresh.assert_awaited_once()
