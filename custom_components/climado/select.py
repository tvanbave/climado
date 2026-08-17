"""Mode-override select for Climado."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

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

    @property
    def extra_state_attributes(self):
        expires_at = self.coordinator.manual_mode_until
        return {"expires_at": expires_at.isoformat() if expires_at else None}

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_manual_mode(option)
        # An explicit mode choice supersedes a respected hand adjustment.
        self.coordinator.clear_manual_hold()
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in SELECT_MODES:
            raw_expiry = last.attributes.get("expires_at")
            expires_at = (
                dt_util.parse_datetime(raw_expiry)
                if isinstance(raw_expiry, str)
                else None
            )
            self.coordinator.restore_manual_mode(last.state, expires_at)
        else:
            self.coordinator.set_manual_mode(MODE_AUTO)
