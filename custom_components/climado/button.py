"""Buttons for Climado: start pre-arrival, clear/resume."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ClimadoCoordinator
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ClimadoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ClimadoPreArrivalButton(coordinator, entry),
            ClimadoClearButton(coordinator, entry),
        ]
    )


class _BaseButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ClimadoCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)


class ClimadoPreArrivalButton(_BaseButton):
    """Start cooling ahead of arrival (uses configured defaults)."""

    _attr_name = "Heading home (pre-cool)"
    _attr_icon = "mdi:home-clock"

    def __init__(self, coordinator: ClimadoCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "prearrival")

    async def async_press(self) -> None:
        self.coordinator.start_prearrival()
        await self.coordinator.async_request_refresh()


class ClimadoClearButton(_BaseButton):
    """Cancel pre-arrival and recompute now."""

    _attr_name = "Resume (clear pre-cool)"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator: ClimadoCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "resume")

    async def async_press(self) -> None:
        self.coordinator.clear_prearrival()
        await self.coordinator.async_request_refresh()
