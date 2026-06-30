"""Config-category number entities for Climado tunables."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NUMBER_TUNABLES
from .coordinator import ClimadoCoordinator
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ClimadoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ClimadoNumber(coordinator, entry, *spec) for spec in NUMBER_TUNABLES
    )


class ClimadoNumber(CoordinatorEntity, RestoreNumber):
    """A tunable scalar; owns the live value, mirrors it into the coordinator."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator, entry, key, name, mn, mx, step, unit, icon, default
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._default = default
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_min_value = mn
        self._attr_native_max_value = mx
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_info = device_info(entry)
        self._attr_native_value = float(coordinator.opt(key, default))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        self.coordinator.set_tunable(self._key, self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.set_tunable(self._key, value)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
