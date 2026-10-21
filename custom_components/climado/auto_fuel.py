"""Opt-in provisional ULO switching through confirmed, separate HVAC commands."""
from datetime import timedelta

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_ELECTRICITY_PLAN, CONF_HEATING_ENABLED, CONF_OUTDOOR_TEMP_SENSOR
from .fuel import running_source, selected_source
from .temperature import temperature_reading

MINIMUM_DWELL = timedelta(minutes=30)
OFF_TIME = timedelta(minutes=5)
TRANSITION_TIMEOUT = timedelta(minutes=20)


def ulo_source(current, tier, outdoor):
    """Retain the selected fuel in the open (2, 4] Celsius hysteresis band."""
    if tier != "ultra_low" or outdoor <= 2:
        return "gas"
    if outdoor > 4:
        return "heat_pump"
    return current


class AutoFuel:
    def __init__(self, coordinator):
        self.c = coordinator
        self.enabled = False
        self.error = None
        self.phase = None
        self.target = None
        self.expected = None
        self.changed = dt_util.utcnow()
        self.started = None
        self.stopped = None
        self.reason = "Automatic fuel selection is off"
        self.outdoor = None
        self._store = Store(coordinator.hass, 1, f"climado.{coordinator.entry.entry_id}.auto_fuel")

    def _context(self):
        return [self.c.windows.climate_entity, self.c._aux_entity,
                self.c.opt(CONF_OUTDOOR_TEMP_SENSOR)]

    async def save(self):
        await self._store.async_save({"context": self._context(), "enabled": self.enabled,
                                      "phase": self.phase, "expected": self.expected,
                                      "error": self.error})

    async def load(self):
        saved = await self._store.async_load()
        if not isinstance(saved, dict) or saved.get("context") != self._context():
            return
        self.enabled = saved.get("enabled") is True
        self.expected = saved.get("expected")
        self.error = saved.get("error")
        # Never replay an interrupted stop/start sequence after a restart.
        if saved.get("phase"):
            self.enabled = False
            self.error = "Fuel change interrupted by restart; check thermostat and re-enable Automatic"
            await self.save()

    def snapshot(self):
        return {"enabled": self.enabled, "phase": self.phase, "target": self.target,
                "reason": self.error or self.reason, "error": self.error,
                "outdoor_c": self.outdoor, "gas_at_c": 2, "heat_pump_above_c": 4,
                "minimum_dwell_minutes": 30, "off_minutes": 5}

    def _ready(self):
        c = self.c
        if c.opt(CONF_ELECTRICITY_PLAN, "ULO") != "ULO":
            return "Automatic fuel selection requires the ULO electricity plan"
        if not c.enabled or not c.opt(CONF_HEATING_ENABLED, False):
            return "Climado heating control is off"
        if c.windows.busy:
            return "Windows open pause or restoration is active"
        if c.feedback.mode_disagrees():
            return "Waiting for local and Ecobee modes to agree"
        if c.feedback.snapshot().get("configured_entity") and not c.feedback.local():
            return "Waiting for fresh local feedback"
        for entity in (c.windows.climate_entity, c._aux_entity):
            state = c.hass.states.get(entity) if entity else None
            if (not state or state.state in ("unknown", "unavailable")
                    or not timedelta(0) <= dt_util.utcnow() - state.last_reported < timedelta(minutes=10)):
                return "Waiting for fresh Ecobee mode and fuel reports"
        outdoor = temperature_reading(c.hass, c.opt(CONF_OUTDOOR_TEMP_SENSOR))
        self.outdoor = outdoor["value"]
        if outdoor["status"] != "ready" or not -60 <= self.outdoor <= 60:
            return "Waiting for a fresh outdoor temperature in Celsius"
        return None

    async def set_enabled(self, enabled):
        if enabled:
            reason = self._ready()
            if reason or self.c.system.pending or self.c.system.error or self.c._hvac_mode() != "heat":
                if reason == "Waiting for fresh Ecobee mode and fuel reports":
                    self.c.feedback.request_refresh()
                raise ServiceValidationError(reason or "Confirm Heat mode before enabling Automatic")
        previous = (self.enabled, self.error, self.phase, self.expected)
        self.enabled, self.error, self.phase = enabled, None, None
        self.expected = selected_source(self.c._hvac_mode(), self.c._aux_selection()) if enabled else None
        try:
            await self.save()
        except Exception:
            self.enabled, self.error, self.phase, self.expected = previous
            raise
        self.changed = dt_util.utcnow()
        self.target = self.stopped = None
        self.reason = "Waiting for minimum source time" if enabled else "Automatic fuel selection is off"

    async def _fail(self, reason):
        self.error = reason
        self.enabled = False
        self.phase = None
        self.stopped = None
        await self.save()

    def _idle(self):
        c = self.c
        cloud_idle = (c._climate_attr("hvac_action") in ("idle", "off", "fan")
                      and running_source(c._climate_attr("equipment_running"),
                                         c._climate_attr("hvac_action")) in ("idle", "fan"))
        local = c.feedback.local()
        return cloud_idle and (not local or local.attributes.get("hvac_action") in ("idle", "off", "fan"))

    async def step(self, tier):
        """Return true while owning an Off-to-Heat handoff, blocking comfort writes."""
        if not self.enabled:
            return False
        c, now = self.c, dt_util.utcnow()
        if c.opt(CONF_ELECTRICITY_PLAN, "ULO") != "ULO":
            if self.phase:
                await self._fail("Electricity plan changed during handoff; check thermostat")
            else:
                await self.set_enabled(False)
            return False
        if not c.enabled or not c.opt(CONF_HEATING_ENABLED, False) or c.windows.busy:
            self.expected = None
            self.changed = now
            if self.phase:
                await self._fail("Fuel change interrupted; check thermostat before re-enabling Automatic")
            else:
                self.reason = "Automatic fuel selection paused"
            return False
        if c.system.error:
            await self._fail("Thermostat command failed; check thermostat before re-enabling Automatic")
            return False
        if self.phase and now - self.started >= TRANSITION_TIMEOUT:
            await self._fail("Fuel change timed out; check thermostat before re-enabling Automatic")
            return False
        if c.system.pending:
            self.reason = "Waiting for thermostat confirmation"
            return bool(self.phase)
        if reason := self._ready():
            self.reason = reason
            if reason == "Waiting for fresh Ecobee mode and fuel reports":
                self.c.feedback.request_refresh()
            self.stopped = None
            return bool(self.phase)
        mode = c._hvac_mode()
        current = selected_source(mode, c._aux_selection())
        if self.phase == "starting":
            if mode == "heat" and current == self.target:
                self.phase = None
                self.expected = current
                self.changed = now
                await self.save()
            else:
                await self._fail("Fuel selection changed during handoff; Automatic disabled")
            return False
        if self.phase == "stopping":
            if mode != "off":
                await self._fail("Thermostat mode changed during handoff; Automatic disabled")
                return False
            if not self._idle() or not c.system.snapshot()["can_start"]:
                self.stopped = None
                self.reason = "Waiting for equipment to stop"
                return True
            self.stopped = self.stopped or now
            if now - self.stopped < OFF_TIME:
                self.reason = "Waiting for five-minute equipment rest"
                return True
            # Re-evaluate the tier and weather after the wait, not just at entry.
            self.target = ulo_source(self.expected, tier, self.outdoor)
            self.phase = "starting"
            await self.save()
            await self._command("heat", self.target)
            return True
        if mode not in ("heat", "off", "cool") or current == "unknown":
            self.reason = "Waiting for a known heating source"
            return False
        if mode != "heat" or (self.expected and current != self.expected):
            await self._fail("Manual system or fuel change; Automatic disabled")
            return False
        self.expected = current
        self.target = ulo_source(current, tier, self.outdoor)
        self.reason = "Keeping current heating source"
        if self.target == current:
            return False
        if now - self.changed < MINIMUM_DWELL:
            self.reason = "Waiting for minimum source time"
            return False
        actual = c._thermostat_actual()
        if (c._manual_hold is not None or c._pending_command or
                (c._last_commanded is not None and actual is not None and not c._matches_command(actual))):
            self.reason = "Waiting for comfort hold or target confirmation"
            return False
        if not self._idle():
            self.reason = "Waiting for the heating cycle to finish"
            return False
        self.phase, self.started, self.stopped = "stopping", now, None
        await self.save()
        await self._command("off")
        return True

    async def _command(self, mode, source=None):
        try:
            await self.c.system.async_set(mode, source)
        except Exception as err:
            await self._fail(f"Fuel command failed: {err}; no automatic retry")
