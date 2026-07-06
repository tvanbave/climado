"""Climado engine: presence -> mode -> setpoint resolution -> ecobee command.

A single ``DataUpdateCoordinator`` evaluates the priority ladder on a 15-minute
tick and on any presence/occupancy state change, then writes the resolved
cooling setpoint to the underlying climate entity.

Scalar settings (setpoints, timeouts, rate knobs, night window) are "tunables":
they are exposed as number/time entities (entity_category=config) which own the
live value and write it into ``self.tunables``; the config entry options provide
the initial seed / fallback. Structural settings (the climate + sensors) stay in
the config entry and are edited via the options flow.

Priority ladder (FR3):
    vacation > manual-away > pre_arrival > away (daytime, or overnight if the
    house was empty at the night boundary) > night (hand off to the ecobee's
    native Sleep comfort / bedroom closed loop) > rate-engine overlay (home)
    > mode default
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AWAY_DELAY,
    CONF_AWAY_TEMP,
    CONF_CLIMATE_ENTITY,
    CONF_COMFORT_HOME,
    CONF_MAIN_TEMP_SENSOR,
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
    CONF_RATE_PLAN,
    CONF_VACATION_TEMP,
    CONF_WORKDAY_SENSOR,
    STRUCTURAL_KEYS,
    DEFAULT_AWAY_DELAY,
    DEFAULT_AWAY_TEMP,
    DEFAULT_COMFORT_HOME,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    DEFAULT_ONPEAK_COAST,
    DEFAULT_PREARRIVAL_LEAD,
    DEFAULT_PREARRIVAL_ONLY_IF_ABOVE,
    DEFAULT_PREARRIVAL_TARGET,
    DEFAULT_PRECOOL_DEPTH,
    DEFAULT_PRECOOL_LEAD,
    DEFAULT_VACATION_TEMP,
    DEVICE_COOL_MAX,
    DEVICE_COOL_MIN,
    DOMAIN,
    MODE_AWAY,
    MODE_DISABLED,
    MODE_HOME,
    MODE_PREARRIVAL,
    MODE_SLEEP,
    MODE_VACATION,
)
from .rate import default_ulo_plan, plan_from_schedule, plan_to_dict, rate_offset

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=15)
_UNAVAILABLE = ("unknown", "unavailable", "", None)


def _parse_time(value) -> time:
    if isinstance(value, time):
        return value
    parts = [int(p) for p in str(value).split(":")]
    while len(parts) < 3:
        parts.append(0)
    return time(parts[0], parts[1], parts[2])


def _in_window(now_t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now_t < end
    return now_t >= start or now_t < end  # crosses midnight


class ClimadoCoordinator(DataUpdateCoordinator):
    """Evaluates climate state for one zone."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}:{entry.title}", update_interval=SCAN_INTERVAL
        )
        self.entry = entry
        self.enabled: bool = True
        self.vacation: bool = False
        self.manual_mode: str = "auto"
        # Tunables owned by the number/time entities; seeded from options.
        self.tunables: dict[str, object] = {}
        self._prearrival_until: datetime | None = None
        self._prearrival_target: float | None = None
        self._last_present: datetime = dt_util.utcnow()
        self._night_active: bool = False
        self._night_away_allowed: bool = False
        self._released: bool = True  # True once we've handed control back after a disable
        self._unsub: list = []
        self._structural = {k: self.opt(k) for k in STRUCTURAL_KEYS}

    def structural_changed(self) -> bool:
        """True if a structural (entity) option changed since last check."""
        current = {k: self.opt(k) for k in STRUCTURAL_KEYS}
        if current != self._structural:
            self._structural = current
            return True
        return False

    # ---- config access ----
    @property
    def options(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    def opt(self, key, default=None):
        value = self.options.get(key, default)
        return default if value is None else value

    def tune(self, key, default=None):
        """Live tunable value (entity-owned), falling back to options/default."""
        if key in self.tunables and self.tunables[key] is not None:
            return self.tunables[key]
        return self.opt(key, default)

    def set_tunable(self, key, value) -> None:
        self.tunables[key] = value

    # ---- lifecycle ----
    async def async_setup_listeners(self) -> None:
        watch = list(self.opt(CONF_PRESENCE_ENTITIES, [])) + list(
            self.opt(CONF_OCCUPANCY_ENTITIES, [])
        )
        if watch:
            self._unsub.append(
                async_track_state_change_event(
                    self.hass, watch, self._handle_sensor_event
                )
            )

    async def async_unload(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()

    @callback
    def _handle_sensor_event(self, event: Event) -> None:
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        # Ignore attribute-only churn (e.g. phone GPS coordinate updates) —
        # only an actual state-value change can affect presence/occupancy.
        if old is not None and new is not None and old.state == new.state:
            return
        self.hass.async_create_task(self.async_request_refresh())

    # ---- pre-arrival API ----
    def start_prearrival(self, lead_minutes=None, target=None, only_if_above=None, force=False) -> bool:
        lead = int(lead_minutes if lead_minutes is not None else self.tune(CONF_PREARRIVAL_LEAD, DEFAULT_PREARRIVAL_LEAD))
        tgt = float(target if target is not None else self.tune(CONF_PREARRIVAL_TARGET, DEFAULT_PREARRIVAL_TARGET))
        threshold = only_if_above if only_if_above is not None else self.tune(CONF_PREARRIVAL_ONLY_IF_ABOVE, DEFAULT_PREARRIVAL_ONLY_IF_ABOVE)
        # `force` = explicit user intent (the physical button): never skip on the
        # only-if-above guard. The guard is for conditional/service callers.
        if not force and threshold is not None:
            current = self._get_float(self.opt(CONF_MAIN_TEMP_SENSOR))
            if current is not None and current <= float(threshold):
                _LOGGER.info("Climado pre-arrival skipped: house %.1f <= %.1f", current, float(threshold))
                return False
        self._prearrival_until = dt_util.utcnow() + timedelta(minutes=lead)
        self._prearrival_target = tgt
        return True

    def clear_prearrival(self) -> None:
        self._prearrival_until = None
        self._prearrival_target = None

    def _prearrival_active(self) -> bool:
        return self._prearrival_until is not None and dt_util.utcnow() < self._prearrival_until

    # ---- helpers ----
    def _get_float(self, entity_id):
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _UNAVAILABLE:
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _is_occupied(self) -> bool:
        for ent in self.opt(CONF_PRESENCE_ENTITIES, []):
            state = self.hass.states.get(ent)
            if state and state.state == "home":
                return True
        for ent in self.opt(CONF_OCCUPANCY_ENTITIES, []):
            state = self.hass.states.get(ent)
            if state and state.state == "on":
                return True
        return False

    def _is_workday(self) -> bool:
        ent = self.opt(CONF_WORKDAY_SENSOR)
        if ent:
            state = self.hass.states.get(ent)
            if state is not None and state.state not in _UNAVAILABLE:
                return state.state == "on"
        return dt_util.now().weekday() < 5

    def _away_elapsed(self) -> bool:
        delay = int(self.tune(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY))
        return (dt_util.utcnow() - self._last_present) >= timedelta(minutes=delay)

    def _plan(self):
        coast = float(self.tune(CONF_ONPEAK_COAST, DEFAULT_ONPEAK_COAST))
        lead = int(self.tune(CONF_PRECOOL_LEAD, DEFAULT_PRECOOL_LEAD))
        depth = float(self.tune(CONF_PRECOOL_DEPTH, DEFAULT_PRECOOL_DEPTH))
        custom = self.opt(CONF_RATE_PLAN)
        if isinstance(custom, dict) and custom.get("weekday") and custom.get("weekend"):
            try:
                return plan_from_schedule(
                    custom["weekday"], custom["weekend"], coast, lead, depth
                )
            except (ValueError, TypeError, KeyError):
                _LOGGER.warning("Climado: invalid stored rate plan; using ULO default")
        return default_ulo_plan(coast, lead, depth)

    # ---- core evaluation ----
    async def _async_update_data(self) -> dict:
        now = dt_util.now()
        occupied = self._is_occupied()
        if occupied:
            self._last_present = dt_util.utcnow()

        night_start = _parse_time(self.tune(CONF_NIGHT_START, DEFAULT_NIGHT_START))
        night_end = _parse_time(self.tune(CONF_NIGHT_END, DEFAULT_NIGHT_END))
        is_night = _in_window(now.time(), night_start, night_end)
        is_workday = self._is_workday()

        # Night away-latch (Nest/ecobee style): only allow Away overnight if the
        # house was already empty/away at the moment night began. Once anyone is
        # present during the night, latch it off for the rest of the night.
        if is_night and not self._night_active:
            self._night_away_allowed = (not occupied) and self._away_elapsed()
        self._night_active = is_night
        if is_night and occupied:
            self._night_away_allowed = False

        if self._prearrival_active() and occupied:
            self.clear_prearrival()

        if not self.enabled:
            # Hand the thermostat cleanly back to its native schedule (once per
            # disable): otherwise our last hold persists ("until you change it")
            # and silently blocks the ecobee's own comfort schedule.
            if not self._released:
                self._released = True
                await self._release_control()
            return self._state(MODE_DISABLED, "disabled", None, None, occupied, is_night)
        self._released = False

        comfort_home = float(self.tune(CONF_COMFORT_HOME, DEFAULT_COMFORT_HOME))
        away_temp = float(self.tune(CONF_AWAY_TEMP, DEFAULT_AWAY_TEMP))
        vacation_temp = float(self.tune(CONF_VACATION_TEMP, DEFAULT_VACATION_TEMP))
        forced = self.manual_mode if self.manual_mode in (
            MODE_HOME, MODE_AWAY, MODE_SLEEP, MODE_VACATION
        ) else None

        plan = self._plan()
        tier = plan.tier_at(now, is_workday)

        if self.vacation or forced == MODE_VACATION:
            mode, action, reason = MODE_VACATION, ("temp", vacation_temp), "vacation"
        elif forced == MODE_AWAY:
            mode, action, reason = MODE_AWAY, ("temp", away_temp), "manual_away"
        elif self._prearrival_active():
            mode, action, reason = MODE_PREARRIVAL, ("temp", float(self._prearrival_target)), "pre_arrival"
        elif (
            forced is None
            and not occupied
            and self._away_elapsed()
            and (not is_night or self._night_away_allowed)
        ):
            mode, action, reason = MODE_AWAY, ("temp", away_temp), "away"
        elif forced == MODE_SLEEP or (is_night and forced != MODE_HOME):
            # Hand control to the ecobee's native Sleep comfort (Bedroom sensor):
            # a true closed loop on the bedroom that reaches target and cycles off,
            # which outperforms a computed main-floor hold for a hot 2nd-floor room.
            # Also honors a manual "sleep" override at any time of day.
            mode, action = MODE_SLEEP, ("preset", "sleep")
            reason = "manual_sleep" if forced == MODE_SLEEP else "night/ecobee-sleep"
        else:
            offset, rtier = rate_offset(plan, now, is_workday)
            mode, action, reason = MODE_HOME, ("temp", comfort_home + offset), f"home/{rtier}"

        if action[0] == "preset":
            applied = await self._apply_preset(action[1])
            target = applied
        else:
            target = action[1]
            applied = await self._apply(target)
        return self._state(
            mode, reason, target, tier, occupied, is_night, applied, plan_to_dict(plan)
        )

    async def _apply(self, target):
        if target is None:
            return None
        ent = self.opt(CONF_CLIMATE_ENTITY)
        state = self.hass.states.get(ent)
        if state is None:
            _LOGGER.warning("Climado: climate entity %s not found", ent)
            return None
        if state.state != "cool":
            _LOGGER.debug("Climado: %s not in cool mode (%s); skipping", ent, state.state)
            return None
        dmin = float(state.attributes.get("min_temp", DEVICE_COOL_MIN))
        dmax = float(state.attributes.get("max_temp", DEVICE_COOL_MAX))
        step = float(state.attributes.get("target_temp_step", 0.5) or 0.5)
        value = min(dmax, max(dmin, float(target)))
        value = round(value / step) * step
        current = state.attributes.get("temperature")
        if current is not None and abs(float(current) - value) < (step / 2):
            return value
        try:
            await self.hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": ent, "temperature": value},
                blocking=False,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Climado: failed to set %s -> %.1f: %s", ent, value, err)
            return None
        _LOGGER.debug("Climado set %s -> %.1f", ent, value)
        return value

    async def _release_control(self):
        """Cancel our hold so the thermostat's native schedule resumes."""
        ent = self.opt(CONF_CLIMATE_ENTITY)
        if self.hass.services.has_service("ecobee", "resume_program"):
            try:
                await self.hass.services.async_call(
                    "ecobee",
                    "resume_program",
                    {"entity_id": ent, "resume_all": True},
                    blocking=False,
                )
                _LOGGER.info("Climado disabled: resumed %s native program", ent)
                return
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Climado: failed to resume program on %s: %s", ent, err)
        else:
            _LOGGER.info(
                "Climado disabled: no resume service for %s; last hold remains", ent
            )

    async def _apply_preset(self, preset):
        """Hand control to an ecobee comfort setting (Sleep -> Bedroom sensor).

        The ecobee then runs its own closed loop on that comfort's assigned
        sensor and cycles off when satisfied. Idempotent: skips if already active.
        """
        ent = self.opt(CONF_CLIMATE_ENTITY)
        state = self.hass.states.get(ent)
        if state is None:
            _LOGGER.warning("Climado: climate entity %s not found", ent)
            return None
        if state.state != "cool":
            _LOGGER.debug("Climado: %s not in cool mode (%s); skipping", ent, state.state)
            return None
        if state.attributes.get("preset_mode") == preset:
            return state.attributes.get("temperature")  # already active; avoid churn
        try:
            await self.hass.services.async_call(
                "climate",
                "set_preset_mode",
                {"entity_id": ent, "preset_mode": preset},
                blocking=False,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Climado: failed to set %s preset -> %s: %s", ent, preset, err)
            return None
        _LOGGER.debug("Climado set %s preset -> %s", ent, preset)
        return state.attributes.get("temperature")

    def _state(self, mode, reason, target, tier, occupied, is_night, applied=None, rate_plan=None) -> dict:
        return {
            "mode": mode,
            "reason": reason,
            "target": target,
            "applied": applied,
            "tier": tier.name if tier is not None else None,
            "tier_id": tier.tier_id if tier is not None else None,
            "rate_plan": rate_plan,
            "occupied": occupied,
            "is_night": is_night,
            "night_away_allowed": self._night_away_allowed,
            "vacation": self.vacation,
            "enabled": self.enabled,
            "manual_mode": self.manual_mode,
            "prearrival_active": self._prearrival_active(),
            "prearrival_until": self._prearrival_until.isoformat()
            if self._prearrival_until
            else None,
        }
