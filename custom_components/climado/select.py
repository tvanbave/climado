"""Mode-override select for Climado."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_AUTO, SELECT_MODES
from .coordinator import ClimadoCoordinator
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ClimadoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ClimadoModeSelect(coordinator, entry)])


class ClimadoModeSelect(CoordinatorEntity, SelectEntity, RestoreEntity):
    """Manual mode override (auto/home/away/sleep/vacation)."""

    _attr_has_entity_name = True
    _attr_name = "Mode"
    _attr_icon = "mdi:home-thermometer"
    _attr_options = SELECT_MODES

    def __init__(self, coordinator: ClimadoCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_mode"
        self._attr_device_info = device_info(entry)

    @property
    def current_option(self) -> str:
        return self.coordinator.manual_mode

    async def async_select_option(self, option: str) -> None:
        self.coordinator.manual_mode = option
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in SELECT_MODES:
            self.coordinator.manual_mode = last.state
        else:
            self.coordinator.manual_mode = MODE_AUTO
