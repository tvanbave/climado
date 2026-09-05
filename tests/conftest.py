"""Home Assistant runtime fixtures; no requests reach a real thermostat."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.util import dt as dt_util

from custom_components.climado.coordinator import ClimadoCoordinator

ZONE = ZoneInfo("America/Toronto")


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        value = datetime(2026, 9, 7, 12, tzinfo=ZONE)

        def set(self, iso):
            self.value = datetime.fromisoformat(iso).replace(tzinfo=ZONE)

        def now(self, time_zone=None):
            return self.value.astimezone(time_zone or ZONE)

        def utcnow(self):
            return self.value.astimezone(timezone.utc)

    clock = Clock()
    old_zone = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(ZONE)
    monkeypatch.setattr(dt_util, "now", clock.now)
    monkeypatch.setattr(dt_util, "utcnow", clock.utcnow)
    yield clock
    dt_util.set_default_time_zone(old_zone)


@pytest_asyncio.fixture
async def engine(tmp_path, monkeypatch, clock):
    hass = HomeAssistant(str(tmp_path))
    frame.async_setup(hass)
    hass.states.async_set("person.test", "home")
    hass.states.async_set("sensor.main", "23")
    hass.states.async_set("sensor.bedroom", "25")
    hass.states.async_set("climate.test", "cool", {
        "temperature": 23.5, "preset_mode": "temp", "hvac_action": "idle",
        "current_temperature": 23.0, "min_temp": 7, "max_temp": 35,
        "target_temp_step": 0.5,
    })
    entry = SimpleNamespace(entry_id="test", title="Test", options={}, data={
        "climate_entity": "climate.test", "main_temp_sensor": "sensor.main",
        "bedroom_temp_sensor": "sensor.bedroom", "presence_entities": ["person.test"],
    })
    coordinator = ClimadoCoordinator(hass, entry)
    await coordinator.async_restore_runtime()
    timer = Mock(side_effect=lambda *args: Mock())
    monkeypatch.setattr("custom_components.climado.coordinator.async_track_point_in_time", timer)

    async def echo(domain, service, data, **kwargs):
        assert kwargs.get("blocking") is True
        attrs = dict(hass.states.get("climate.test").attributes)
        if service == "set_temperature":
            attrs.update(temperature=data["temperature"], preset_mode="temp")
        elif service == "set_preset_mode":
            attrs.update(temperature=23.1, preset_mode=data["preset_mode"])
        elif service == "resume_program":
            attrs.update(preset_mode="home")
        hass.states.async_set("climate.test", "cool", attrs)

    service = AsyncMock(side_effect=echo)
    real_call = type(hass.services).async_call
    monkeypatch.setattr(type(hass.services), "async_call", service)
    yield SimpleNamespace(c=coordinator, hass=hass, clock=clock, service=service,
                          echo=echo, timer=timer, entry=entry, real_call=real_call)
    await coordinator.async_unload()
    await hass.async_stop(force=True)
