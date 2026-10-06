"""Explicit manual system commands, separate from automatic comfort control."""
from datetime import timedelta

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .fuel import running_source


class SystemControl:
    """Send one user-requested mode command and wait for observed confirmation."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.pending = None
        self.error = None
        self._reported_at = None
        self._store = Store(coordinator.hass, 1, f"climado.{coordinator.entry.entry_id}.system")
        self._saved = None

    def _data(self):
        return {
            "climate_entity": self.coordinator.windows.climate_entity,
            "aux_entity": self.coordinator._aux_entity,
            "pending": {**self.pending, "until": self.pending["until"].isoformat()} if self.pending else None,
            "reported_at": self._reported_at.isoformat() if self._reported_at else None,
            "error": self.error,
        }

    async def async_save(self):
        data = self._data()
        if data != self._saved:
            await self._store.async_save(data)
            self._saved = data

    async def async_load(self):
        saved = await self._store.async_load()
        if not isinstance(saved, dict) or saved.get("climate_entity") != self.coordinator.windows.climate_entity:
            return
        self.error = saved.get("error")
        request = saved.get("pending")
        if request and not isinstance(request, dict):
            self.error = "Saved system request could not be recovered; no command replayed"
            return
        if request:
            until_raw, reported_raw = request.get("until"), saved.get("reported_at")
            until = dt_util.parse_datetime(until_raw) if isinstance(until_raw, str) else None
            reported = dt_util.parse_datetime(reported_raw) if isinstance(reported_raw, str) else None
            if (saved.get("aux_entity") != self.coordinator._aux_entity
                    or request.get("mode") not in ("off", "cool", "heat")
                    or (request.get("mode") == "heat" and request.get("source") not in ("gas", "heat_pump"))
                    or not until or not reported or until.tzinfo is None or reported.tzinfo is None):
                self.error = "Saved system request could not be recovered; no command replayed"
            else:
                self.pending = {**request, "until": until}
                self._reported_at = reported
        self._saved = saved

    async def async_cancel(self):
        self.pending = None
        self.error = None
        await self.async_save()

    def snapshot(self):
        c = self.coordinator
        climate = c.hass.states.get(c.windows.climate_entity)
        aux = c.hass.states.get(c._aux_entity) if c._aux_entity else None
        available = climate is not None and climate.state not in ("unknown", "unavailable")
        modes = climate.attributes.get("hvac_modes", []) if available else []
        aux_ready = aux is not None and aux.state in ("on", "off")
        current = climate.state if available else None
        reason = None
        if c.windows.busy:
            reason = "Windows open pause or restoration is active"
        elif self.pending:
            reason = "Waiting for thermostat confirmation"
        elif not available:
            reason = "Thermostat unavailable"
        elif current != "off":
            reason = "Mode and source changes locked while system is active"
        elif not timedelta(0) <= dt_util.utcnow() - climate.last_reported < timedelta(minutes=10):
            reason = "Thermostat reading is stale"
        elif climate.attributes.get("hvac_action") not in ("idle", "off", "fan") or running_source(
            climate.attributes.get("equipment_running"), climate.attributes.get("hvac_action")
        ) not in ("idle", "fan"):
            reason = "Waiting for equipment to stop or report its state"
        return {
            "entry_id": c.entry.entry_id,
            "hvac_mode": current,
            "modes": [mode for mode in ("off", "cool", "heat") if mode in modes],
            "can_stop": available and "off" in modes and not c.windows.busy,
            "can_start": reason is None,
            "source_available": aux_ready,
            "heat_source": ("gas" if aux.state == "on" else "heat_pump") if aux_ready and current == "heat" else None,
            "blocked_reason": reason,
            "pending": {key: value for key, value in self.pending.items() if key != "until"} if self.pending else None,
            "error": self.error,
        }

    def confirm(self):
        if not self.pending:
            return
        c = self.coordinator
        mode, source = self.pending["mode"], self.pending["source"]
        climate = c.hass.states.get(c.windows.climate_entity)
        fresh_report = climate is not None and climate.last_reported > self._reported_at
        if fresh_report and c._hvac_mode() == mode and (mode != "heat" or c._aux_selection() is (source == "gas")):
            self.pending = None
            self.error = None
        elif dt_util.utcnow() >= self.pending["until"]:
            self.pending = None
            self.error = "Thermostat did not confirm the requested mode; no automatic retry"

    async def async_set(self, mode, source=None, context=None):
        c = self.coordinator
        self.confirm()
        state = self.snapshot()
        if mode not in ("off", "cool", "heat") or mode not in state["modes"]:
            raise ServiceValidationError("System mode is unavailable or unsupported")
        if mode != "heat" and source is not None:
            raise ServiceValidationError("A heating source is only valid with Heat mode")
        if mode == "heat" and source not in ("heat_pump", "gas"):
            raise ServiceValidationError("Choose Heat pump or Gas furnace; automatic selection is unavailable")
        if c.windows.busy:
            raise ServiceValidationError(state["blocked_reason"])
        if not self.pending and mode == state["hvac_mode"] and (mode != "heat" or source == state["heat_source"]):
            return
        if mode != "off" and not state["can_start"]:
            raise ServiceValidationError(state["blocked_reason"])
        if mode == "heat" and not state["source_available"]:
            raise ServiceValidationError("Auxiliary heat switch is unavailable")
        if mode == "heat" and source == "gas":
            # Ecobee Aux ON selects auxHeatOnly and starts heating. Aux OFF can
            # restore heat_cool, so heat-pump requests use explicit climate Heat.
            domain, service, data = "switch", "turn_on", {"entity_id": c._aux_entity}
        else:
            domain, service, data = "climate", "set_hvac_mode", {"entity_id": c.windows.climate_entity, "hvac_mode": mode}
        if not c.hass.services.has_service(domain, service):
            raise ServiceValidationError("Thermostat service is unavailable")
        previous_pending, previous_reported = self.pending, self._reported_at
        self.error = None
        self._reported_at = c.hass.states.get(c.windows.climate_entity).last_reported
        self.pending = {"mode": mode, "source": source, "until": dt_util.utcnow() + timedelta(minutes=5)}
        try:
            await self.async_save()
        except Exception:
            self.pending, self._reported_at = previous_pending, previous_reported
            self.error = "Could not save system request; no command sent"
            raise
        c.clear_prearrival()
        c.clear_manual_hold()
        try:
            await c.hass.services.async_call(domain, service, data, blocking=True, context=context)
        except Exception as err:
            # Delivery can be uncertain on timeout. Keep the confirmation guard
            # but never retry automatically or claim that the mode was applied.
            self.error = str(err) or type(err).__name__
            raise
        self.confirm()
