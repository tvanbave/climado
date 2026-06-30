"""Switches for Climado: master enable and vacation hold."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
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
            ClimadoEnableSwitch(coordinator, entry),
            ClimadoVacationSwitch(coordinator, entry),
        ]
    )


class _BaseSwitch(CoordinatorEntity, SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ClimadoCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)


class ClimadoEnableSwitch(_BaseSwitch):
    """Master enable; off = Climado stops controlling the thermostat."""

    _attr_name = "Climado control"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: ClimadoCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "enabled")

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.enabled = True
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.enabled = False
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.enabled = last is None or last.state != "off"


class ClimadoVacationSwitch(_BaseSwitch):
    """Vacation hold (uses the vacation temperature)."""

    _attr_name = "Vacation"
    _attr_icon = "mdi:bag-suitcase"

    def __init__(self, coordinator: ClimadoCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "vacation")

    @property
    def is_on(self) -> bool:
        return self.coordinator.vacation

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.vacation = True
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.vacation = False
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.vacation = last is not None and last.state == "on"
