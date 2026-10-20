"""Validated indoor observations shared by status, prearrival and alerts."""
from datetime import timedelta
from math import isfinite

from homeassistant.util import dt as dt_util

INDOOR_MAX_AGE = timedelta(minutes=15)


def temperature_reading(hass, entity_id, *, attribute=None, now=None):
    now = now or dt_util.utcnow()
    result = {"entity_id": entity_id, "value": None, "status": "not_configured",
              "reported_at": None}
    if not entity_id:
        return result
    state = hass.states.get(entity_id)
    result["status"] = "unavailable"
    if state is None or state.state in ("unknown", "unavailable"):
        return result
    result["reported_at"] = state.last_reported.isoformat()
    unit = (state.attributes.get("temperature_unit", hass.config.units.temperature_unit)
            if attribute else state.attributes.get("unit_of_measurement"))
    if unit != "°C":
        return {**result, "status": "unsupported_unit"}
    age = now - state.last_reported
    if age < timedelta(0):
        return {**result, "status": "future_report"}
    if age >= INDOOR_MAX_AGE:
        return {**result, "status": "stale"}
    try:
        value = float(state.attributes.get(attribute) if attribute else state.state)
        if not isfinite(value):
            raise ValueError("Non-finite indoor temperature")
    except (ValueError, TypeError, OverflowError):
        return {**result, "status": "invalid"}
    return {**result, "value": value, "status": "ready"}
