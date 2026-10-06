"""Advisory heating checks. Never change HVAC, fan or window-pause state."""
from datetime import timedelta
from math import isfinite

from homeassistant.components import persistent_notification
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALERT_DELAY,
    CONF_ALERT_HEAT_MINUTES,
    CONF_ALERT_LOW_TEMP,
    CONF_ALERT_MIN_RISE,
    CONF_ALERT_NOTIFICATIONS,
    CONF_ALERTS_ENABLED,
    CONF_BEDROOM_TEMP_SENSOR,
    CONF_MAIN_TEMP_SENSOR,
)

ISSUES = ("windows_cold", "command_failed", "heat_not_improving")


class HeatingAlerts:
    def __init__(self, coordinator):
        self.c = coordinator
        self._cold_since = None
        self._cold_seen = None
        self._heat_sample = None
        self._notified = set()
        self._initialized = False

    def _setting(self, key, default, minimum, maximum):
        try:
            value = float(self.c.tune(key, default))
            return value if isfinite(value) and minimum <= value <= maximum else default
        except (TypeError, ValueError):
            return default

    def _temperature(self, entity_id, attribute=None):
        state = self.c.hass.states.get(entity_id) if entity_id else None
        if not state or state.state in ("unknown", "unavailable"):
            return None
        unit = state.attributes.get("temperature_unit", self.c.hass.config.units.temperature_unit) if attribute else state.attributes.get("unit_of_measurement")
        if unit != "°C" or not timedelta(0) <= dt_util.utcnow() - state.last_reported < timedelta(minutes=15):
            return None
        try:
            value = float(state.attributes.get(attribute) if attribute else state.state)
            return value if isfinite(value) else None
        except (TypeError, ValueError):
            return None

    def _notify(self, issues):
        active = {item["code"] for item in issues} if self.c.opt(CONF_ALERT_NOTIFICATIONS, True) else set()
        for code in ISSUES:
            notification_id = f"climado_{self.c.entry.entry_id}_{code}"
            if code in active and code not in self._notified:
                message = next(item["message"] for item in issues if item["code"] == code)
                persistent_notification.async_create(self.c.hass, message, f"Climado: {self.c.entry.title}", notification_id)
            elif code not in active and (code in self._notified or not self._initialized):
                persistent_notification.async_dismiss(self.c.hass, notification_id)
        self._notified = active
        self._initialized = True

    def update(self, data):
        enabled = self.c.opt(CONF_ALERTS_ENABLED, True)
        if not enabled:
            self._cold_since = self._heat_sample = None
            self._notify([])
            return {"status": "disabled", "issues": []}
        now = dt_util.utcnow()
        issues = []
        threshold = self._setting(CONF_ALERT_LOW_TEMP, 16, 5, 22)
        delay = timedelta(minutes=self._setting(CONF_ALERT_DELAY, 10, 1, 120))
        rooms = [self._temperature(self.c.opt(key)) for key in (CONF_MAIN_TEMP_SENSOR, CONF_BEDROOM_TEMP_SENSOR)]
        readings = [value for value in rooms if value is not None]
        if data.get("windows_open") and readings and min(readings) < threshold:
            if self._cold_seen is None or not timedelta(0) <= now - self._cold_seen < timedelta(minutes=15):
                self._cold_since = None
            self._cold_since = self._cold_since or now
            self._cold_seen = now
            if now - self._cold_since >= delay:
                issues.append({"code": "windows_cold", "message": f"Windows open is still active and an indoor sensor has stayed below {threshold:g} C for at least {delay.total_seconds() / 60:g} minutes. Heating remains paused."})
        else:
            self._cold_since = None
            self._cold_seen = None

        system = data.get("system_control", {})
        error = system.get("error") or data.get("windows_error") or data.get("command_error")
        if error:
            issues.append({"code": "command_failed", "message": f"Thermostat command needs attention: {error}"})

        temp = self._temperature(self.c.windows.climate_entity, "current_temperature")
        target = data.get("target")
        if target is None:
            target = data.get("thermostat_target")
        try:
            target = float(target)
            target = target if isfinite(target) else None
        except (TypeError, ValueError):
            target = None
        heating = (data.get("hvac_mode") == "heat" and data.get("hvac_action") == "heating"
                   and data.get("running_source") in ("heat_pump", "gas", "mixed")
                   and not self.c.windows.busy and not system.get("pending"))
        if heating and temp is not None and target is not None and temp < target - .5:
            climate = self.c.hass.states.get(self.c.windows.climate_entity)
            context = (data.get("selected_source"), target, climate.attributes.get("preset_mode"), str(climate.attributes.get("active_sensors")))
            sample = self._heat_sample
            # Do not bridge a restart, sensor gap, source change or sensor handoff.
            if not sample or sample["context"] != context or now - sample["seen"] >= timedelta(minutes=15):
                sample = self._heat_sample = {"context": context, "start": now, "temp": temp, "seen": now}
            sample["seen"] = now
            minutes = self._setting(CONF_ALERT_HEAT_MINUTES, 60, 15, 180)
            if now - sample["start"] >= timedelta(minutes=minutes):
                rise = temp - sample["temp"]
                if rise < self._setting(CONF_ALERT_MIN_RISE, .3, .1, 3):
                    issues.append({"code": "heat_not_improving", "message": f"Heating has been reported continuously for at least {minutes:g} minutes, but temperature rose only {rise:.1f} C and remains below target. Check equipment and airflow; Climado has not changed fuel."})
                else:
                    self._heat_sample = {"context": context, "start": now, "temp": temp, "seen": now}
        else:
            self._heat_sample = None
        self._notify(issues)
        monitoring = "unavailable" if (data.get("windows_open") and not readings) or (heating and temp is None) else "ready"
        return {"status": issues[0]["code"] if issues else "clear", "monitoring": monitoring, "issues": issues}
