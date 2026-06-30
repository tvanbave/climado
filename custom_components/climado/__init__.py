"""The Climado integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .const import (
    ATTR_LEAD_MINUTES,
    ATTR_ONLY_IF_ABOVE,
    ATTR_TARGET,
    DOMAIN,
    PLATFORMS,
    SERVICE_CLEAR_PREARRIVAL,
    SERVICE_START_PREARRIVAL,
)
from .coordinator import ClimadoCoordinator

_LOGGER = logging.getLogger(__name__)

_START_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_LEAD_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=0, max=720)),
        vol.Optional(ATTR_TARGET): vol.All(vol.Coerce(float), vol.Range(min=10, max=33.5)),
        vol.Optional(ATTR_ONLY_IF_ABOVE): vol.All(vol.Coerce(float), vol.Range(min=10, max=40)),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Climado from a config entry."""
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
            for service in (SERVICE_START_PREARRIVAL, SERVICE_CLEAR_PREARRIVAL):
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

    hass.services.async_register(
        DOMAIN, SERVICE_START_PREARRIVAL, _handle_start, schema=_START_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_PREARRIVAL, _handle_clear, schema=vol.Schema({})
    )
