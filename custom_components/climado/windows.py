"""Persistent, explicit HVAC pause. Fan settings are never changed."""
from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util


class WindowsPause:
    """Own only the HVAC-off request and its corresponding mode restoration."""

    def __init__(self, hass, climate_entity, entry_id):
        self.hass = hass
        self.climate_entity = climate_entity
        self.active = False
        self.restore = None
        self.error = None
        self.retry_at = None
        self._command = None
        self._store = Store(hass, 1, f"climado.{entry_id}.windows")

    @property
    def busy(self):
        return self.active or self.restore is not None

    async def async_load(self):
        saved = await self._store.async_load()
        if saved and saved.get("climate_entity") == self.climate_entity:
            self.active = saved.get("active") is True
            self.restore = saved.get("restore")

    async def _save(self):
        await self._store.async_save({
            "climate_entity": self.climate_entity,
            "active": self.active,
            "restore": self.restore,
        })

    async def async_set_active(self, active):
        if active == self.active:
            return
        previous = self.active
        self.active = active
        try:
            await self._save()
        except Exception:
            self.active = previous
            raise
        if not self.restore or self.restore.get("phase") != "pausing":
            self._command = None
            self.retry_at = None
        self.error = None

    async def _finish_restore(self):
        previous = self.restore
        self.restore = None
        try:
            await self._save()
        except Exception:
            self.restore = previous
            raise

    def _aux_entity(self):
        registry = er.async_get(self.hass)
        climate = registry.async_get(self.climate_entity)
        if not climate or not climate.device_id:
            return None
        matches = [
            entry.entity_id for entry in er.async_entries_for_device(registry, climate.device_id)
            if entry.domain == "switch" and entry.platform == "ecobee"
            and entry.unique_id.endswith("_aux_heat_only")
        ]
        if len(matches) > 1:
            raise ValueError("Cannot identify a unique auxiliary heat switch")
        return matches[0] if matches else None

    def _aux_state(self, entity_id):
        state = self.hass.states.get(entity_id) if entity_id else None
        if not state or state.state not in ("on", "off"):
            raise ValueError("Auxiliary heat switch is unavailable")
        return state.state == "on"

    async def async_step(self, released):
        """Return True when paused/restoring, False after a confirmed restoration."""
        if not self.busy:
            return False
        try:
            state = self.hass.states.get(self.climate_entity)
            if not state or state.state not in ("heat", "cool", "heat_cool", "off"):
                raise ValueError("Thermostat unavailable; waiting to apply Windows open")
            if self.active and self.restore is None:
                aux_entity = self._aux_entity() if state.state == "heat" else None
                aux = self._aux_state(aux_entity) if aux_entity else False
                self.restore = {"mode": state.state, "aux_entity": aux_entity, "aux": aux, "released": released, "phase": "pausing"}
                # Save the original mode durably before the first off command.
                try:
                    await self._save()
                except Exception:
                    self.restore = None
                    raise
            if self.restore is None:
                return False
            pausing = self.active or self.restore.get("phase") == "pausing"
            target = "off" if pausing else self.restore["mode"]
            aux_entity = self.restore.get("aux_entity")
            matches = state.state == target
            if not pausing and target == "heat" and aux_entity:
                matches = matches and self._aux_state(aux_entity) == self.restore["aux"]
            if matches:
                self.error = None
                self._command = None
                self.retry_at = None
                if pausing:
                    if self.restore.get("phase") != "paused":
                        self.restore["phase"] = "paused"
                        await self._save()
                    if not self.active:
                        return await self.async_step(released)
                else:
                    await self._finish_restore()
                    return False
                return True
            command = ("switch", "turn_on", aux_entity) if (
                not pausing and self.restore["aux"] and target == "heat"
            ) else ("climate", "set_hvac_mode", target)
            if command == self._command and self.retry_at and dt_util.utcnow() < self.retry_at:
                return True
            self._command = command
            self.retry_at = dt_util.utcnow() + timedelta(minutes=5)
            domain, service, value = command
            data = {"entity_id": value} if domain == "switch" else {
                "entity_id": self.climate_entity, "hvac_mode": value,
            }
            await self.hass.services.async_call(domain, service, data, blocking=True)
            self.error = None
            # Confirmation comes from the next state snapshot, not service success.
            state = self.hass.states.get(self.climate_entity)
            confirmed = state is not None and state.state == target
            if not pausing and target == "heat" and aux_entity:
                confirmed = confirmed and self._aux_state(aux_entity) == self.restore["aux"]
            if confirmed:
                self._command = None
                self.retry_at = None
                if pausing:
                    self.restore["phase"] = "paused"
                    await self._save()
                    if not self.active:
                        return await self.async_step(released)
                else:
                    await self._finish_restore()
                    return False
            return True
        except Exception as err:  # noqa: BLE001 - remain paused on service/storage errors
            self.error = str(err) or type(err).__name__
            self.retry_at = dt_util.utcnow() + timedelta(minutes=1)
            return True
