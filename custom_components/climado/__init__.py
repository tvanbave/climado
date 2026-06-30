"""The Climado integration."""
from __future__ import annotations

import logging
import os

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    ATTR_LEAD_MINUTES,
    ATTR_ONLY_IF_ABOVE,
    ATTR_PLAN,
    ATTR_TARGET,
    CONF_RATE_PLAN,
    DOMAIN,
    PLATFORMS,
    SERVICE_CLEAR_PREARRIVAL,
    SERVICE_SET_RATE_PLAN,
    SERVICE_START_PREARRIVAL,
    VERSION,
)
from .coordinator import ClimadoCoordinator
from .rate import normalize_schedule

CARD_URL = "/climado_static/climado-card.js"

_LOGGER = logging.getLogger(__name__)

_START_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_LEAD_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=0, max=720)),
        vol.Optional(ATTR_TARGET): vol.All(vol.Coerce(float), vol.Range(min=10, max=33.5)),
        vol.Optional(ATTR_ONLY_IF_ABOVE): vol.All(vol.Coerce(float), vol.Range(min=10, max=40)),
    }
)

_SET_PLAN_SCHEMA = vol.Schema({vol.Required(ATTR_PLAN): dict})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Climado from a config entry."""
    await _register_frontend(hass)
    coordinator = ClimadoCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await coordinator.async_setup_listeners()
    # Forward platforms first so the number/time config entities load and
    # restore their values into coordinator.tunables before the first evaluate.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_config_entry_first_refresh()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _register_services(hass)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: ClimadoCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_unload()
        if not hass.data[DOMAIN]:
            for service in (
                SERVICE_START_PREARRIVAL,
                SERVICE_CLEAR_PREARRIVAL,
                SERVICE_SET_RATE_PLAN,
            ):
                hass.services.async_remove(DOMAIN, service)
    return unloaded


def _register_services(hass: HomeAssistant) -> None:
    """Register integration-wide services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_START_PREARRIVAL):
        return

    async def _handle_start(call: ServiceCall) -> None:
        for coordinator in hass.data.get(DOMAIN, {}).values():
            coordinator.start_prearrival(
                call.data.get(ATTR_LEAD_MINUTES),
                call.data.get(ATTR_TARGET),
                call.data.get(ATTR_ONLY_IF_ABOVE),
            )
            await coordinator.async_request_refresh()

    async def _handle_clear(call: ServiceCall) -> None:
        for coordinator in hass.data.get(DOMAIN, {}).values():
            coordinator.clear_prearrival()
            await coordinator.async_request_refresh()

    async def _handle_set_plan(call: ServiceCall) -> None:
        raw = call.data[ATTR_PLAN]
        try:
            norm = {
                "weekday": normalize_schedule(raw["weekday"]),
                "weekend": normalize_schedule(raw["weekend"]),
            }
        except (KeyError, TypeError, ValueError) as err:
            raise vol.Invalid(f"invalid rate plan: {err}") from err
        for coordinator in hass.data.get(DOMAIN, {}).values():
            entry = coordinator.entry
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_RATE_PLAN: norm}
            )

    hass.services.async_register(
        DOMAIN, SERVICE_START_PREARRIVAL, _handle_start, schema=_START_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_PREARRIVAL, _handle_clear, schema=vol.Schema({})
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_RATE_PLAN, _handle_set_plan, schema=_SET_PLAN_SCHEMA
    )


async def _register_frontend(hass: HomeAssistant) -> None:
    """Serve and auto-load the climado-card Lovelace module (once)."""
    if hass.data.get(f"{DOMAIN}_frontend"):
        return
    hass.data[f"{DOMAIN}_frontend"] = True
    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
    await hass.http.async_register_static_paths(
        [StaticPathConfig("/climado_static", frontend_dir, False)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
