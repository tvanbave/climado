"""Shared entity helpers for Climado."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_NAME, DEFAULT_NAME, DOMAIN


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Group all Climado entities for a zone under one device."""
    name = entry.options.get(CONF_NAME) or entry.data.get(CONF_NAME) or DEFAULT_NAME
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Climado {name}",
        manufacturer="Climado",
        model="Presence-aware climate controller",
    )
