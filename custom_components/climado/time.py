"""Config-category time entities for Climado (night window)."""
from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TIME_TUNABLES
from .coordinator import ClimadoCoordinator
from .entity import device_info


def _parse(value) -> dt_time:
    if isinstance(value, dt_time):
        return value
    parts = [int(p) for p in str(value).split(":")]
    while len(parts) < 3:
        parts.append(0)
    return dt_time(parts[0], parts[1], parts[2])


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ClimadoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ClimadoTime(coordinator, entry, *spec) for spec in TIME_TUNABLES
    )


class ClimadoTime(CoordinatorEntity, RestoreEntity, TimeEntity):
    """A tunable time-of-day; mirrors its value into the coordinator."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, key, name, icon, default) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)
        self._attr_native_value = _parse(coordinator.opt(key, default))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable", "", None):
            try:
                self._attr_native_value = _parse(last.state)
            except (ValueError, TypeError):
                pass
        self.coordinator.set_tunable(self._key, self._attr_native_value.strftime("%H:%M:%S"))

    async def async_set_value(self, value: dt_time) -> None:
        self._attr_native_value = value
        self.coordinator.set_tunable(self._key, value.strftime("%H:%M:%S"))
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
