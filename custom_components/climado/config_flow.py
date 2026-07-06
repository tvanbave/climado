"""Config and options flow for Climado.

Every entity field uses a domain/device-class-filtered ``EntitySelector`` (the
Alarmo pattern): the user picks the thermostat, temperature sensors, presence
trackers and occupancy/motion sensors from live lists — no entity IDs are typed
or hardcoded.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AWAY_DELAY,
    CONF_AWAY_TEMP,
    CONF_BEDROOM_TEMP_SENSOR,
    CONF_CLIMATE_ENTITY,
    CONF_COMFORT_HOME,
    CONF_MAIN_TEMP_SENSOR,
    CONF_NAME,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_OCCUPANCY_ENTITIES,
    CONF_ONPEAK_COAST,
    CONF_PREARRIVAL_LEAD,
    CONF_PREARRIVAL_ONLY_IF_ABOVE,
    CONF_PREARRIVAL_TARGET,
    CONF_PRECOOL_DEPTH,
    CONF_PRECOOL_LEAD,
    CONF_PRESENCE_ENTITIES,
    CONF_VACATION_TEMP,
    CONF_WORKDAY_SENSOR,
    DEFAULT_AWAY_DELAY,
    DEFAULT_AWAY_TEMP,
    DEFAULT_COMFORT_HOME,
    DEFAULT_NAME,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    DEFAULT_ONPEAK_COAST,
    DEFAULT_PREARRIVAL_LEAD,
    DEFAULT_PREARRIVAL_ONLY_IF_ABOVE,
    DEFAULT_PREARRIVAL_TARGET,
    DEFAULT_PRECOOL_DEPTH,
    DEFAULT_PRECOOL_LEAD,
    DEFAULT_VACATION_TEMP,
    DOMAIN,
    STRUCTURAL_KEYS,
)


def _entity(domain, multiple: bool = False, device_class: str | None = None):
    kwargs: dict[str, Any] = {"domain": domain, "multiple": multiple}
    if device_class:
        kwargs["device_class"] = device_class
    return selector.EntitySelector(selector.EntitySelectorConfig(**kwargs))


def _num(minv, maxv, step, unit=None):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minv,
            max=maxv,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


def _build_schema(current: dict) -> vol.Schema:
    def dft(key, fallback):
        value = current.get(key, fallback)
        return value if value is not None else vol.UNDEFINED

    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=dft(CONF_NAME, DEFAULT_NAME)): selector.TextSelector(),
            vol.Required(CONF_CLIMATE_ENTITY, default=dft(CONF_CLIMATE_ENTITY, None)): _entity("climate"),
            vol.Required(CONF_MAIN_TEMP_SENSOR, default=dft(CONF_MAIN_TEMP_SENSOR, None)): _entity("sensor", device_class="temperature"),
            vol.Optional(CONF_BEDROOM_TEMP_SENSOR, default=dft(CONF_BEDROOM_TEMP_SENSOR, None)): _entity("sensor", device_class="temperature"),
            vol.Optional(CONF_PRESENCE_ENTITIES, default=current.get(CONF_PRESENCE_ENTITIES, [])): _entity(["device_tracker", "person"], multiple=True),
            vol.Optional(CONF_OCCUPANCY_ENTITIES, default=current.get(CONF_OCCUPANCY_ENTITIES, [])): _entity("binary_sensor", multiple=True),
            vol.Optional(CONF_WORKDAY_SENSOR, default=dft(CONF_WORKDAY_SENSOR, None)): _entity("binary_sensor"),
            vol.Optional(CONF_COMFORT_HOME, default=dft(CONF_COMFORT_HOME, DEFAULT_COMFORT_HOME)): _num(10, 33.5, 0.5, "°C"),
            vol.Optional(CONF_AWAY_TEMP, default=dft(CONF_AWAY_TEMP, DEFAULT_AWAY_TEMP)): _num(10, 33.5, 0.5, "°C"),
            vol.Optional(CONF_VACATION_TEMP, default=dft(CONF_VACATION_TEMP, DEFAULT_VACATION_TEMP)): _num(10, 33.5, 0.5, "°C"),
            vol.Optional(CONF_AWAY_DELAY, default=dft(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY)): _num(5, 240, 5, "min"),
            vol.Optional(CONF_NIGHT_START, default=dft(CONF_NIGHT_START, DEFAULT_NIGHT_START)): selector.TimeSelector(),
            vol.Optional(CONF_NIGHT_END, default=dft(CONF_NIGHT_END, DEFAULT_NIGHT_END)): selector.TimeSelector(),
            vol.Optional(CONF_ONPEAK_COAST, default=dft(CONF_ONPEAK_COAST, DEFAULT_ONPEAK_COAST)): _num(0, 6, 0.5, "°C"),
            vol.Optional(CONF_PRECOOL_LEAD, default=dft(CONF_PRECOOL_LEAD, DEFAULT_PRECOOL_LEAD)): _num(0, 240, 15, "min"),
            vol.Optional(CONF_PRECOOL_DEPTH, default=dft(CONF_PRECOOL_DEPTH, DEFAULT_PRECOOL_DEPTH)): _num(0, 6, 0.5, "°C"),
            vol.Optional(CONF_PREARRIVAL_LEAD, default=dft(CONF_PREARRIVAL_LEAD, DEFAULT_PREARRIVAL_LEAD)): _num(0, 360, 15, "min"),
            vol.Optional(CONF_PREARRIVAL_TARGET, default=dft(CONF_PREARRIVAL_TARGET, DEFAULT_PREARRIVAL_TARGET)): _num(10, 30, 0.5, "°C"),
            vol.Optional(CONF_PREARRIVAL_ONLY_IF_ABOVE, default=dft(CONF_PREARRIVAL_ONLY_IF_ABOVE, DEFAULT_PREARRIVAL_ONLY_IF_ABOVE)): _num(10, 40, 0.5, "°C"),
        }
    )


def _structural_schema(current: dict) -> vol.Schema:
    """Options-flow schema: only the structural entity pickers.

    Scalar tunables (setpoints, timeouts, rate knobs, night window) are edited as
    config-category number/time entities on the device, so they are intentionally
    omitted here to keep a single source of truth.
    """

    def dft(key, fallback):
        value = current.get(key, fallback)
        return value if value is not None else vol.UNDEFINED

    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=dft(CONF_NAME, DEFAULT_NAME)): selector.TextSelector(),
            vol.Required(CONF_CLIMATE_ENTITY, default=dft(CONF_CLIMATE_ENTITY, None)): _entity("climate"),
            vol.Required(CONF_MAIN_TEMP_SENSOR, default=dft(CONF_MAIN_TEMP_SENSOR, None)): _entity("sensor", device_class="temperature"),
            vol.Optional(CONF_BEDROOM_TEMP_SENSOR, default=dft(CONF_BEDROOM_TEMP_SENSOR, None)): _entity("sensor", device_class="temperature"),
            vol.Optional(CONF_PRESENCE_ENTITIES, default=current.get(CONF_PRESENCE_ENTITIES, [])): _entity(["device_tracker", "person"], multiple=True),
            vol.Optional(CONF_OCCUPANCY_ENTITIES, default=current.get(CONF_OCCUPANCY_ENTITIES, [])): _entity("binary_sensor", multiple=True),
            vol.Optional(CONF_WORKDAY_SENSOR, default=dft(CONF_WORKDAY_SENSOR, None)): _entity("binary_sensor"),
        }
    )


class ClimadoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CLIMATE_ENTITY])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get(CONF_NAME, DEFAULT_NAME), data=user_input
            )
        return self.async_show_form(step_id="user", data_schema=_build_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ClimadoOptionsFlow(config_entry)


class ClimadoOptionsFlow(config_entries.OptionsFlow):
    """Edit every parameter from the UI (no YAML)."""

    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            # Preserve non-form option keys (e.g. the saved rate plan) — replacing
            # options wholesale with just the structural fields would silently
            # wipe a custom schedule saved via climado.set_rate_plan.
            preserved = {
                k: v
                for k, v in self._entry.options.items()
                if k not in STRUCTURAL_KEYS
            }
            return self.async_create_entry(title="", data={**preserved, **user_input})
        current = {**self._entry.data, **self._entry.options}
        return self.async_show_form(step_id="init", data_schema=_structural_schema(current))
