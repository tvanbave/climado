"""Read-only local observations and bounded, public-API cloud refreshes."""
import asyncio
import logging
import math
from datetime import timedelta

from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import CONF_CLIMATE_ENTITY, CONF_LOCAL_FEEDBACK_ENTITY

_LOGGER = logging.getLogger(__name__)
MAX_AGE = timedelta(minutes=10)
REFRESH_INTERVAL = timedelta(minutes=3)


class ThermostatFeedback:
    """Never send controls to the optional feedback thermostat."""

    def __init__(self, coordinator):
        self.c = coordinator
        self._unsub = None
        self._task = None
        self._last_refresh = None
        self._until = None
        self.refresh_error = None
        self._closed = False

    def cloud(self):
        return self.c.hass.states.get(self.c.opt(CONF_CLIMATE_ENTITY))

    def local(self):
        entity = self.c.opt(CONF_LOCAL_FEEDBACK_ENTITY)
        if not entity or entity == self.c.opt(CONF_CLIMATE_ENTITY):
            return None
        state = self.c.hass.states.get(entity)
        if (state is None or state.state not in ("off", "heat", "cool", "heat_cool")
                or not timedelta(0) <= dt_util.utcnow() - state.last_reported < MAX_AGE):
            return None
        return state

    def local_mode_after(self, mode, requested_at):
        state = self.local()
        return bool(state and requested_at and state.last_reported > requested_at and state.state == mode)

    def local_target_after(self, command, requested_at):
        state, cloud = self.local(), self.cloud()
        if (not state or not cloud or not requested_at or command is None or command[0] != "temp"
                or state.last_reported <= requested_at or state.state != cloud.state):
            return False
        value = self._number(state.attributes.get("temperature"))
        return value is not None and abs(value - command[1]) < 0.3

    @staticmethod
    def _number(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (ValueError, TypeError):
            return None

    def mode_disagrees(self):
        local, cloud = self.local(), self.cloud()
        return bool(local and cloud and local.state != cloud.state)

    def snapshot(self):
        local, cloud = self.local(), self.cloud()
        observed = local or cloud
        configured = self.c.opt(CONF_LOCAL_FEEDBACK_ENTITY)
        fresh = bool(observed and observed.state not in ("unknown", "unavailable")
                     and timedelta(0) <= dt_util.utcnow() - observed.last_reported < MAX_AGE)
        attrs = observed.attributes if fresh else {}
        return {
            "configured_entity": configured,
            "entity_id": observed.entity_id if observed else None,
            "source": "local" if local else "cloud",
            "local_status": "ready" if local else "unavailable_or_stale" if configured else "not_configured",
            "reported_at": observed.last_reported.isoformat() if observed else None,
            "fresh": fresh,
            "hvac_mode": observed.state if fresh else None,
            "hvac_action": attrs.get("hvac_action"),
            "temperature": self._number(attrs.get("current_temperature")),
            "target": self._number(attrs.get("temperature")),
            "mode_disagreement": self.mode_disagrees(),
            "target_confirmed_locally": self.local_target_after(self.c._pending_command, self.c._commanded_at),
            "refresh_error": self.refresh_error,
        }

    def request_refresh(self):
        """At most two read attempts per five-minute window; never bypass throttle."""
        if self._closed or not self.c.hass.services.has_service("homeassistant", "update_entity"):
            return
        self._until = dt_util.utcnow() + timedelta(minutes=5)
        self.refresh_error = None
        self._schedule()

    def _schedule(self):
        if self._closed or self._task is not None or self._unsub is not None or self._until is None:
            return
        due = dt_util.utcnow() + timedelta(seconds=2)
        if self._last_refresh:
            due = max(due, self._last_refresh + REFRESH_INTERVAL)
        if due >= self._until:
            return
        self._unsub = async_track_point_in_time(self.c.hass, self._start, due)

    def _start(self, _now):
        self._unsub = None
        if self._closed:
            return
        self._task = self.c.hass.async_create_background_task(self._refresh(), "Climado feedback refresh")

    async def _refresh(self):
        self._last_refresh = dt_util.utcnow()
        entities = list(dict.fromkeys(entity for entity in (
            self.c.opt(CONF_CLIMATE_ENTITY), self.c._aux_entity,
        ) if entity))
        try:
            async with asyncio.timeout(30):
                await self.c.hass.services.async_call(
                    "homeassistant", "update_entity", {"entity_id": entities}, blocking=True,
                )
            self.refresh_error = None
        except Exception as err:
            self.refresh_error = str(err) or type(err).__name__
            _LOGGER.debug("Climado feedback refresh failed: %s", self.refresh_error, exc_info=True)
        finally:
            try:
                if not self._closed:
                    # A read failure does not mean the equipment command failed.
                    await self.c.async_request_refresh()
            finally:
                self._task = None
                self._schedule()

    async def async_close(self):
        self._closed = True
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
