"""Status sensors for Climado."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ClimadoCoordinator
from .entity import device_info


@dataclass(frozen=True, kw_only=True)
class ClimadoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], object]


SENSORS: tuple[ClimadoSensorDescription, ...] = (
    ClimadoSensorDescription(
        key="effective_mode",
        name="Effective mode",
        icon="mdi:home-account",
        value_fn=lambda d: d.get("mode"),
    ),
    ClimadoSensorDescription(
        key="reason",
        name="Control reason",
        icon="mdi:information-outline",
        value_fn=lambda d: d.get("reason"),
    ),
    ClimadoSensorDescription(
        key="resolved_target",
        name="Resolved target",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:thermometer",
        value_fn=lambda d: d.get("target"),
    ),
    ClimadoSensorDescription(
        key="rate_tier",
        name="Rate tier",
        icon="mdi:cash-clock",
        value_fn=lambda d: d.get("tier"),
    ),
    ClimadoSensorDescription(
        key="presence",
        name="Presence",
        icon="mdi:motion-sensor",
        value_fn=lambda d: "occupied" if d.get("occupied") else "away",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ClimadoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ClimadoSensor(coordinator, entry, desc) for desc in SENSORS)


class ClimadoSensor(CoordinatorEntity, SensorEntity):
    """A single read-only status value from the engine."""

    _attr_has_entity_name = True
    entity_description: ClimadoSensorDescription

    def __init__(
        self,
        coordinator: ClimadoCoordinator,
        entry: ConfigEntry,
        description: ClimadoSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        if self.entity_description.key == "effective_mode":
            return {
                "applied_setpoint": data.get("applied"),
                "is_night": data.get("is_night"),
                "night_away_allowed": data.get("night_away_allowed"),
                "prearrival_active": data.get("prearrival_active"),
                "prearrival_until": data.get("prearrival_until"),
                "manual_override": data.get("manual_mode"),
                "manual_hold_active": data.get("manual_hold_active"),
                "manual_hold_until": data.get("manual_hold_until"),
                "manual_hold_value": data.get("manual_hold_value"),
                "main_temp": data.get("main_temp"),
                "bedroom_temp": data.get("bedroom_temp"),
                "hvac_action": data.get("hvac_action"),
                "regulating": data.get("regulating"),
                "next_transition": data.get("next_transition"),
            }
        if self.entity_description.key == "rate_tier":
            return {
                "plan": data.get("rate_plan"),
                "tier_id": data.get("tier_id"),
                "profile": data.get("rate_profile"),
                "is_workday": data.get("is_workday"),
            }
        return None
