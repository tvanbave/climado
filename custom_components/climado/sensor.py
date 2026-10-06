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
        key="heating_alert", name="Heating alert", icon="mdi:alert-outline",
        value_fn=lambda d: d.get("heating_alerts", {}).get("status"),
    ),
    ClimadoSensorDescription(
        key="selected_fuel", name="Selected fuel", icon="mdi:radiator",
        value_fn=lambda d: d.get("selected_source"),
    ),
    ClimadoSensorDescription(
        key="running_equipment", name="Running equipment", icon="mdi:hvac",
        value_fn=lambda d: d.get("running_source"),
    ),
    ClimadoSensorDescription(
        key="fuel_recommendation", name="Fuel cost recommendation", icon="mdi:cash-check",
        value_fn=lambda d: (d.get("heat_advisory", {}).get("preferred_source") or "no_preference")
        if d.get("heat_advisory", {}).get("status") == "ready" else None,
    ),
    ClimadoSensorDescription(
        key="heat_pump_cost", name="Heat pump delivered heat cost", icon="mdi:cash",
        native_unit_of_measurement="CAD/kWh heat",
        value_fn=lambda d: d.get("heat_advisory", {}).get("heat_pump_per_kwh"),
    ),
    ClimadoSensorDescription(
        key="furnace_cost_min", name="Furnace delivered heat cost minimum", icon="mdi:cash",
        native_unit_of_measurement="CAD/kWh heat",
        value_fn=lambda d: d.get("heat_advisory", {}).get("furnace_per_kwh_min"),
    ),
    ClimadoSensorDescription(
        key="furnace_cost_max", name="Furnace delivered heat cost maximum", icon="mdi:cash",
        native_unit_of_measurement="CAD/kWh heat",
        value_fn=lambda d: d.get("heat_advisory", {}).get("furnace_per_kwh_max"),
    ),
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
                "system_control": data.get("system_control"),
                "heating_alerts": data.get("heating_alerts"),
                "hvac_mode": data.get("hvac_mode"),
                "windows_open": data.get("windows_open"),
                "windows_restoring": data.get("windows_restoring"),
                "windows_pending": data.get("windows_pending"),
                "windows_error": data.get("windows_error"),
                "heating_enabled": data.get("heating_enabled"),
                "fuel_control": data.get("fuel_control"),
                "selected_source": data.get("selected_source"),
                "running_source": data.get("running_source"),
                "aux_heat_entity": data.get("aux_heat_entity"),
                "fuel_discovery_error": data.get("fuel_discovery_error"),
                "aux_heat": data.get("aux_heat"),
                "equipment_running": data.get("equipment_running"),
                "applied_setpoint": data.get("applied"),
                "command_pending": data.get("command_pending"),
                "command_error": data.get("command_error"),
                "thermostat_target": data.get("thermostat_target"),
                "control_temperature": data.get("control_temperature"),
                "thermostat_updated_at": data.get("thermostat_updated_at"),
                "is_night": data.get("is_night"),
                "night_away_allowed": data.get("night_away_allowed"),
                "prearrival_active": data.get("prearrival_active"),
                "prearrival_until": data.get("prearrival_until"),
                "manual_override": data.get("manual_mode"),
                "manual_override_until": data.get("manual_mode_until"),
                "manual_hold_active": data.get("manual_hold_active"),
                "manual_hold_until": data.get("manual_hold_until"),
                "manual_hold_value": data.get("manual_hold_value"),
                "main_temp": data.get("main_temp"),
                "bedroom_temp": data.get("bedroom_temp"),
                "hvac_action": data.get("hvac_action"),
                "regulating": data.get("regulating"),
                "next_transition": data.get("next_transition"),
            }
        if self.entity_description.key == "fuel_recommendation":
            return data.get("heat_advisory", {})
        if self.entity_description.key == "heating_alert":
            return data.get("heating_alerts", {})
        if self.entity_description.key == "rate_tier":
            return {
                "plan": data.get("rate_plan"),
                "tier_id": data.get("tier_id"),
                "profile": data.get("rate_profile"),
                "is_workday": data.get("is_workday"),
            }
        return None
