"""Climado engine: presence -> mode -> setpoint resolution -> ecobee command.

A single ``DataUpdateCoordinator`` evaluates the priority ladder on a 15-minute
fallback tick, at exact time boundaries, and on relevant state changes, then
writes the resolved seasonal setpoint to the underlying climate entity.

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

import asyncio
import logging
import math
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALERTS_ENABLED,
    CONF_AWAY_DELAY,
    CONF_AWAY_TEMP,
    CONF_BEDROOM_TEMP_SENSOR,
    CONF_CLIMATE_ENTITY,
    CONF_COMFORT_HOME,
    CONF_HEATING_ENABLED,
    CONF_AUX_HEAT_SWITCH,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_HEAT_COST,
    CONF_HEAT_HOME,
    CONF_HEAT_AWAY,
    CONF_HEAT_VACATION,
    CONF_HEAT_PREARRIVAL,
    DEFAULT_HEAT_HOME,
    DEFAULT_HEAT_AWAY,
    DEFAULT_HEAT_VACATION,
    DEFAULT_HEAT_PREARRIVAL,
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
    MODE_AUTO,
    MODE_DISABLED,
    MODE_HOME,
    MODE_MANUAL_HOLD,
    MODE_PREARRIVAL,
    MODE_SLEEP,
    MODE_VACATION,
)
from .rate import default_ulo_plan, plan_from_schedule, plan_to_dict, rate_offset
from .windows import WindowsPause
from .fuel import aux_selection, find_aux_entity, running_source, selected_source
from .advisory import heat_advisory
from .system import SystemControl
from .alerts import HeatingAlerts

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=15)
_UNAVAILABLE = ("unknown", "unavailable", "", None)
# Grace after our own write before external-change detection re-arms — covers
# the ecobee integration's polling lag (state can trail a command by ~3 min).
_MANUAL_GRACE = timedelta(minutes=5)
_RETRY_DELAY = timedelta(minutes=1)


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
        self.manual_mode: str = MODE_AUTO
        self.manual_mode_until: datetime | None = None
        # Tunables owned by the number/time entities; seeded from options.
        self.tunables: dict[str, object] = {}
        self._prearrival_until: datetime | None = None
        self._prearrival_target: float | None = None
        self._absence_since: datetime | None = None
        self._occupied: bool | None = None
        self._night_window_start: datetime | None = None
        self._night_away_allowed: bool = False
        self._released: bool = True  # True once we've handed control back after a disable
        # Manual-hold respect: what we last commanded vs. what the thermostat
        # reports; a mismatch (outside the grace window) means a hand adjustment.
        self._last_commanded: tuple | None = None
        self._commanded_at: datetime | None = None
        self._pending_command: tuple | None = None
        self._retry_at: datetime | None = None
        self._release_retry_at: datetime | None = None
        self._command_error: str | None = None
        self._manual_hold: tuple | None = None
        self._manual_hold_until: datetime | None = None
        self._unsub: list = []
        self._boundary_unsub = None
        self._structural = {k: self.opt(k) for k in STRUCTURAL_KEYS if k != CONF_HEATING_ENABLED}
        self._store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.runtime")
        self._saved_runtime: dict | None = None
        self._evaluation_lock = asyncio.Lock()
        self._hvac_context: tuple | None = None
        self._aux_entity = self.opt(CONF_AUX_HEAT_SWITCH)
        self._fuel_discovery_error = None
        self.windows = WindowsPause(hass, self.opt(CONF_CLIMATE_ENTITY), entry.entry_id, self._aux_entity)
        self.system = SystemControl(self)
        self.alerts = HeatingAlerts(self)
        self._last_fuel_observation = None

    def structural_changed(self) -> bool:
        """True if a structural (entity) option changed since last check."""
        current = {k: self.opt(k) for k in STRUCTURAL_KEYS if k != CONF_HEATING_ENABLED}
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

    def _cost_options(self) -> dict:
        value = self.opt(CONF_HEAT_COST, {})
        return value if isinstance(value, dict) else {}

    def tune(self, key, default=None):
        """Live tunable value (entity-owned), falling back to options/default."""
        if key in self.tunables and self.tunables[key] is not None:
            return self.tunables[key]
        return self.opt(key, default)

    def set_tunable(self, key, value) -> None:
        self.tunables[key] = value

    def set_manual_mode(self, mode: str) -> None:
        """Set a mode override; comfort overrides expire at their next boundary."""
        self.manual_mode = mode
        self.manual_mode_until = self._manual_mode_expiry(mode)

    def restore_manual_mode(
        self, mode: str, expires_at: datetime | None
    ) -> None:
        """Restore a mode and its persisted expiry after a restart."""
        if mode not in (MODE_HOME, MODE_SLEEP):
            self.manual_mode = mode
            self.manual_mode_until = None
            return
        if expires_at is not None:
            expires_at = dt_util.as_utc(expires_at)
            if expires_at <= dt_util.utcnow():
                self.manual_mode = MODE_AUTO
                self.manual_mode_until = None
                return
            self.manual_mode = mode
            self.manual_mode_until = expires_at
            return
        # Upgrade path for states saved before timed overrides existed. If its
        # boundary already passed in the current day/night phase, do not revive
        # the formerly persistent override for another full cycle.
        now = dt_util.now()
        night_start = _parse_time(
            self.tune(CONF_NIGHT_START, DEFAULT_NIGHT_START)
        )
        night_end = _parse_time(self.tune(CONF_NIGHT_END, DEFAULT_NIGHT_END))
        is_night = _in_window(now.time(), night_start, night_end)
        if (mode == MODE_HOME and is_night) or (
            mode == MODE_SLEEP and not is_night
        ):
            self.manual_mode = MODE_AUTO
            self.manual_mode_until = None
            return
        self.set_manual_mode(mode)

    def _manual_mode_expiry(self, mode: str) -> datetime | None:
        if mode == MODE_HOME:
            boundary = _parse_time(
                self.tune(CONF_NIGHT_START, DEFAULT_NIGHT_START)
            )
        elif mode == MODE_SLEEP:
            boundary = _parse_time(self.tune(CONF_NIGHT_END, DEFAULT_NIGHT_END))
        else:
            return None
        now = dt_util.now()
        local = now.replace(
            hour=boundary.hour,
            minute=boundary.minute,
            second=boundary.second,
            microsecond=0,
        )
        if local <= now:
            local += timedelta(days=1)
        return dt_util.as_utc(local)

    # ---- lifecycle ----
    def _runtime_context(self) -> dict:
        return {
            key: self.opt(key)
            for key in (CONF_CLIMATE_ENTITY, CONF_PRESENCE_ENTITIES, CONF_OCCUPANCY_ENTITIES)
        }

    async def async_restore_runtime(self) -> None:
        """Restore departure timing and this night's latch before evaluating."""
        if not self._aux_entity:
            try:
                self._aux_entity = find_aux_entity(self.hass, self.opt(CONF_CLIMATE_ENTITY))
            except ValueError as err:
                self._fuel_discovery_error = str(err)
        self.windows.aux_entity = self._aux_entity
        await self.windows.async_load()
        await self.system.async_load()
        if self.windows.busy:
            await self.system.async_cancel()
        saved = await self._store.async_load()
        if not saved or saved.get("context") != self._runtime_context():
            return
        self._occupied = saved.get("occupied")
        for key in ("absence_since", "night_window_start"):
            raw = saved.get(key)
            value = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
            setattr(self, f"_{key}", dt_util.as_utc(value) if value else None)
        self._night_away_allowed = saved.get("night_away_allowed", False)
        self._saved_runtime = saved

    def _runtime_data(self) -> dict:
        return {
            "context": self._runtime_context(),
            "occupied": self._occupied,
            "absence_since": self._absence_since.isoformat() if self._absence_since else None,
            "night_window_start": self._night_window_start.isoformat() if self._night_window_start else None,
            "night_away_allowed": self._night_away_allowed,
        }

    def _save_runtime(self) -> None:
        data = self._runtime_data()
        if data != self._saved_runtime:
            self._saved_runtime = data
            self._store.async_delay_save(self._runtime_data, 1)

    async def async_setup_listeners(self) -> None:
        watch = list(self.opt(CONF_PRESENCE_ENTITIES, [])) + list(
            self.opt(CONF_OCCUPANCY_ENTITIES, [])
        )
        for key in (CONF_WORKDAY_SENSOR, CONF_CLIMATE_ENTITY, CONF_MAIN_TEMP_SENSOR, CONF_BEDROOM_TEMP_SENSOR, CONF_OUTDOOR_TEMP_SENSOR):
            if entity_id := self.opt(key):
                watch.append(entity_id)
        if self._aux_entity:
            watch.append(self._aux_entity)
        if gas_sensor := self._cost_options().get("gas_usage_sensor"):
            watch.append(gas_sensor)
        watch = list(dict.fromkeys(watch))
        if watch:
            self._unsub.append(
                async_track_state_change_event(
                    self.hass, watch, self._handle_sensor_event
                )
            )

    async def async_unload(self) -> None:
        self._cancel_boundary_refresh()
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        await self._store.async_save(self._runtime_data())
        await self.system.async_save()

    @callback
    def _handle_sensor_event(self, event: Event) -> None:
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        if old is not None and new is not None and old.state == new.state:
            if event.data.get("entity_id") == self.opt(CONF_OUTDOOR_TEMP_SENSOR):
                relevant = ("unit_of_measurement",)
            elif event.data.get("entity_id") == self.opt(CONF_CLIMATE_ENTITY):
                relevant = ("temperature", "preset_mode", "hvac_action", "current_temperature", "active_sensors", "aux_heat", "equipment_running")
            else:
                return
            if all(old.attributes.get(k) == new.attributes.get(k) for k in relevant):
                return
        # Capture departure at the event, before the coordinator's debounce.
        self._update_presence()
        self._save_runtime()
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _handle_boundary(self, _now: datetime) -> None:
        """Refresh when a time-dependent control boundary is reached."""
        self._boundary_unsub = None
        self.hass.async_create_task(self.async_refresh())

    def _cancel_boundary_refresh(self) -> None:
        if self._boundary_unsub is not None:
            self._boundary_unsub()
            self._boundary_unsub = None

    def _schedule_boundary_refresh(
        self,
        now: datetime,
        night_start: time,
        night_end: time,
        plan,
        is_workday: bool,
    ) -> None:
        """Schedule the next rate, pre-cool, night, or profile boundary."""

        def next_at(boundary: time) -> datetime:
            candidate = now.replace(
                hour=boundary.hour,
                minute=boundary.minute,
                second=boundary.second,
                microsecond=0,
            )
            return candidate if candidate > now else candidate + timedelta(days=1)

        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        candidates = [next_at(night_start), next_at(night_end), midnight]
        if self.opt(CONF_ALERTS_ENABLED, True) and (self.windows.busy or self._hvac_mode() == "heat" or self.system.error):
            candidates.append(now + timedelta(minutes=1))
        if self.system.pending:
            candidates.extend([now + timedelta(seconds=5), dt_util.as_local(self.system.pending["until"])])
        cost_options = self._cost_options()
        if cost_options.get("enabled"):
            # State reports can renew freshness without a state_changed event.
            candidates.append(now + timedelta(minutes=1))
            outdoor = self.hass.states.get(self.opt(CONF_OUTDOOR_TEMP_SENSOR, ""))
            try:
                max_age = float(cost_options.get("outdoor_max_age_minutes", 30))
            except (ValueError, TypeError, OverflowError):
                max_age = 0
            if outdoor is not None and 1 <= max_age <= 120:
                expiry = outdoor.last_reported + timedelta(minutes=max_age)
                if expiry > dt_util.as_utc(now):
                    candidates.append(dt_util.as_local(expiry))
        deadlines = [self._prearrival_until, self._manual_hold_until, self._retry_at, self._release_retry_at, self.windows.retry_at]
        if self._absence_since is not None:
            deadlines.append(self._absence_since + timedelta(minutes=int(self.tune(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY))))
        for deadline in deadlines:
            if deadline is not None and deadline > dt_util.as_utc(now):
                candidates.append(dt_util.as_local(deadline))
        if self.manual_mode_until is not None:
            override_end = dt_util.as_local(self.manual_mode_until)
            if override_end > now:
                candidates.append(override_end)
        blocks = plan.weekday if is_workday else plan.weekend
        for start, _end, tier_id in blocks:
            rate_start = next_at(start)
            candidates.append(rate_start)
            lead = plan.tiers[tier_id].precool_lead
            precool_start = rate_start - timedelta(minutes=lead)
            if lead and rate_start.date() == now.date() and precool_start > now:
                candidates.append(precool_start)

        self._cancel_boundary_refresh()
        boundary = min(candidates)
        self._boundary_unsub = async_track_point_in_time(
            self.hass, self._handle_boundary, dt_util.as_utc(boundary)
        )

    # ---- pre-arrival API ----
    def start_prearrival(self, lead_minutes=None, target=None, only_if_above=None, force=False, only_if_below=None) -> bool:
        self._sync_hvac_context()
        if self.windows.busy or not self.enabled or self._control_blocked():
            return False
        heating = self._hvac_mode() == "heat"
        if (heating and only_if_above is not None) or (not heating and only_if_below is not None):
            raise ValueError("Pre-arrival threshold does not match the thermostat mode")
        lead = int(lead_minutes if lead_minutes is not None else self.tune(CONF_PREARRIVAL_LEAD, DEFAULT_PREARRIVAL_LEAD))
        default_target = self.tune(CONF_HEAT_PREARRIVAL, DEFAULT_HEAT_PREARRIVAL) if heating else self.tune(CONF_PREARRIVAL_TARGET, DEFAULT_PREARRIVAL_TARGET)
        tgt = float(target if target is not None else default_target)
        if not math.isfinite(tgt) or not 0 <= lead <= 720:
            raise ValueError("Pre-arrival needs a finite target and duration from 0 to 720 minutes")
        threshold = only_if_above if only_if_above is not None else self.tune(CONF_PREARRIVAL_ONLY_IF_ABOVE, DEFAULT_PREARRIVAL_ONLY_IF_ABOVE)
        if heating:
            threshold = only_if_below if only_if_below is not None else tgt
        # `force` = explicit user intent (the physical button): never skip on the
        # only-if-above guard. The guard is for conditional/service callers.
        if not force and threshold is not None:
            current = self._get_float(self.opt(CONF_MAIN_TEMP_SENSOR))
            if not math.isfinite(float(threshold)):
                raise ValueError("Pre-arrival threshold must be finite")
            if current is None or not math.isfinite(current) or (current >= float(threshold) if heating else current <= float(threshold)):
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

    def _is_occupied(self) -> bool | None:
        unknown = False
        for ent in self.opt(CONF_PRESENCE_ENTITIES, []):
            state = self.hass.states.get(ent)
            if state and state.state == "home":
                return True
            unknown |= state is None or state.state in _UNAVAILABLE
        for ent in self.opt(CONF_OCCUPANCY_ENTITIES, []):
            state = self.hass.states.get(ent)
            if state and state.state == "on":
                return True
            unknown |= state is None or state.state in _UNAVAILABLE
        return None if unknown else False

    def _update_presence(self) -> bool:
        occupied = self._is_occupied()
        if occupied is not None:
            if occupied:
                self._absence_since = None
                self._night_away_allowed = False
            elif self._occupied is not False or self._absence_since is None:
                self._absence_since = dt_util.utcnow()
            self._occupied = occupied
        # Do not infer a departure from entities still loading at startup.
        return self._occupied is not False

    def _is_workday(self) -> bool:
        ent = self.opt(CONF_WORKDAY_SENSOR)
        if ent:
            state = self.hass.states.get(ent)
            if state is not None and state.state not in _UNAVAILABLE:
                return state.state == "on"
        return dt_util.now().weekday() < 5

    def _away_elapsed(self) -> bool:
        delay = int(self.tune(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY))
        return self._absence_since is not None and (dt_util.utcnow() - self._absence_since) >= timedelta(minutes=delay)

    # ---- manual-hold respect ----
    def clear_manual_hold(self) -> None:
        """Drop any respected manual hold (explicit user action or expiry).

        Also re-baselines command tracking: the thermostat still carries the
        user's hold at this point, so without a reset the very next evaluation
        would see actual != last-commanded and instantly re-latch the hold
        (Resume would never resume; expiry would re-arm forever). With the
        baseline cleared, detection stays disarmed until the engine's next
        write re-establishes it.
        """
        self._manual_hold = None
        self._manual_hold_until = None
        self._last_commanded = None
        self._pending_command = None
        self._retry_at = None
        self._command_error = None

    def _thermostat_actual(self) -> tuple | None:
        """The thermostat's current commanded state as ("preset", p) / ("temp", t)."""
        state = self.hass.states.get(self.opt(CONF_CLIMATE_ENTITY))
        if state is None or state.state in _UNAVAILABLE:
            return None
        preset = state.attributes.get("preset_mode")
        if preset and preset not in ("temp", "none"):
            return ("preset", preset)
        temp = state.attributes.get("temperature")
        try:
            return ("temp", float(temp)) if temp is not None else None
        except (ValueError, TypeError):
            return None

    def _matches_command(self, actual: tuple | None) -> bool:
        return self._commands_match(self._last_commanded, actual)

    @staticmethod
    def _commands_match(cmd: tuple | None, actual: tuple | None) -> bool:
        if cmd is None or actual is None:
            return False
        if cmd[0] != actual[0]:
            return False
        if cmd[0] == "preset":
            return cmd[1] == actual[1]
        return abs(float(cmd[1]) - float(actual[1])) < 0.3

    def _next_boundary(self, now: datetime, night_start: time, night_end: time) -> datetime:
        """Next night-window edge after ``now`` (the 'next transition' a hold lasts to)."""
        candidates = []
        for t in (night_start, night_end):
            local = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
            if local <= now:
                local += timedelta(days=1)
            candidates.append(local)
        return dt_util.as_utc(min(candidates))

    def _climate_attr(self, key):
        state = self.hass.states.get(self.opt(CONF_CLIMATE_ENTITY))
        return state.attributes.get(key) if state and state.state not in _UNAVAILABLE else None

    def _hvac_mode(self):
        state = self.hass.states.get(self.opt(CONF_CLIMATE_ENTITY))
        return state.state if state else "unavailable"

    def _control_blocked(self):
        mode = self._hvac_mode()
        if mode not in ("heat", "cool"):
            return "thermostat_off" if mode == "off" else "unsupported_hvac_mode"
        if mode == "heat" and not self.opt(CONF_HEATING_ENABLED, False):
            return "heating_not_enabled"
        if mode == "heat":
            # All Climado temperature entities currently use Celsius.
            unit = self._climate_attr("temperature_unit") or self.hass.config.units.temperature_unit
            if unit != "°C":
                return "unsupported_temperature_unit"
        return None

    def _sync_hvac_context(self):
        context = self._fuel_context()
        if self._hvac_context is not None and self._hvac_context != context:
            self.clear_prearrival()
            self.clear_manual_hold()
            self._release_retry_at = None
            self._commanded_at = None
            self._released = True
        self._hvac_context = context

    def _aux_selection(self):
        return aux_selection(self.hass, self.opt(CONF_CLIMATE_ENTITY), self._aux_entity)

    def _fuel_context(self):
        return (self._hvac_mode(), self._aux_selection())

    def _next_transition(self, now, night_start, night_end, is_night, plan, is_workday):
        """Soonest upcoming mode/rate change, as {at (UTC iso), label} for the card."""

        def next_at(t: time) -> datetime:
            d = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            return d if d > now else d + timedelta(days=1)

        events: list[tuple[datetime, str]] = []
        if is_night:
            events.append((next_at(night_end), "Day"))
        else:
            events.append((next_at(night_start), "Sleep"))
            for start, _end, tier_id in (plan.weekday if is_workday else plan.weekend):
                st = next_at(start)
                events.append((st, plan.tiers[tier_id].name))
                lead = plan.tiers[tier_id].precool_lead
                if lead:
                    events.append((st - timedelta(minutes=lead), "Pre-cool"))
        events = [(w, l) for w, l in events if w > now]
        if not events:
            return None
        when, label = min(events, key=lambda e: e[0])
        return {"at": dt_util.as_utc(when).isoformat(), "label": label}

    def _record_command(self, command: tuple, wrote: bool) -> None:
        if wrote:
            self._pending_command = command
            self._commanded_at = dt_util.utcnow()
            self._retry_at = self._commanded_at + _MANUAL_GRACE
            # Only branches that outrank a manual hold can write while one is
            # active (away/vacation/pre-arrival/forced). Once the engine has
            # overwritten the thermostat, the hand adjustment is moot — drop it
            # so control resumes normally afterwards. (Inline, NOT via
            # clear_manual_hold(): that would null the baseline we just set.)
            self._manual_hold = None
            self._manual_hold_until = None
        else:
            self._last_commanded = command
            self._pending_command = None
            self._retry_at = None
        self._command_error = None

    def _confirm_pending(self) -> None:
        if self._pending_command and self._commands_match(self._pending_command, self._thermostat_actual()):
            self._record_command(self._pending_command, wrote=False)

    async def _send_command(self, command: tuple) -> bool:
        """Wait for the service, then require a matching reported state."""
        if self._control_blocked():
            return False
        context = self._fuel_context()
        if command == self._pending_command and self._retry_at and dt_util.utcnow() < self._retry_at:
            return False
        service = "set_temperature" if command[0] == "temp" else "set_preset_mode"
        key = "temperature" if command[0] == "temp" else "preset_mode"
        self._pending_command = command
        try:
            await self.hass.services.async_call(
                "climate", service,
                {"entity_id": self.opt(CONF_CLIMATE_ENTITY), key: command[1]},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            if context != self._fuel_context():
                self._sync_hvac_context()
                return False
            self._command_error = str(err) or type(err).__name__
            self._retry_at = dt_util.utcnow() + _RETRY_DELAY
            _LOGGER.error("Climado command %s failed: %s", command, err)
            return False
        if context != self._fuel_context():
            self._sync_hvac_context()
            return False
        self._record_command(command, wrote=True)
        self._confirm_pending()
        return self._pending_command is None

    def _plan(self):
        coast = float(self.tune(CONF_ONPEAK_COAST, DEFAULT_ONPEAK_COAST))
        lead = int(self.tune(CONF_PRECOOL_LEAD, DEFAULT_PRECOOL_LEAD))
        depth = float(self.tune(CONF_PRECOOL_DEPTH, DEFAULT_PRECOOL_DEPTH))
        if self._hvac_mode() != "cool":
            # Fuel selection is still manual; no electricity-driven heating offsets.
            coast, lead, depth = 0.0, 0, 0.0
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
    async def async_set_system_mode(self, mode, source=None, context=None):
        try:
            async with self._evaluation_lock:
                await self.system.async_set(mode, source, context)
        finally:
            await self.async_refresh()

    async def async_set_windows_open(self, active):
        async with self._evaluation_lock:
            await self.windows.async_set_active(active)
            await self.system.async_cancel()
            self.clear_prearrival()
            self.clear_manual_hold()
        await self.async_refresh()

    async def _async_update_data(self) -> dict:
        # Boundary and entity refreshes may overlap while a service is running.
        async with self._evaluation_lock:
            try:
                data = await self._evaluate()
                now = dt_util.now()
                tier = self._plan().tier_at(now, self._is_workday())
                data["heat_advisory"] = heat_advisory(
                    self.hass, self.opt(CONF_HEAT_COST, {}), self.opt(CONF_OUTDOOR_TEMP_SENSOR), tier.tier_id, now,
                )
                advisory = data["heat_advisory"]
                advisory.update(observation_only=True, selected_source=data.get("selected_source"))
                observation = (advisory["status"], advisory.get("preferred_source"), advisory["reason"], data.get("selected_source"))
                if advisory["status"] != "disabled" and observation != self._last_fuel_observation:
                    _LOGGER.info("Climado fuel observation (no switching): status=%s preferred=%s reason=%s selected=%s", *observation)
                self._last_fuel_observation = observation
                data["heating_alerts"] = self.alerts.update(data)
                await self.system.async_save()
                return data
            finally:
                self._save_runtime()
                self._schedule_boundary_refresh(
                    dt_util.now(),
                    _parse_time(self.tune(CONF_NIGHT_START, DEFAULT_NIGHT_START)),
                    _parse_time(self.tune(CONF_NIGHT_END, DEFAULT_NIGHT_END)),
                    self._plan(), self._is_workday(),
                )

    async def _evaluate(self) -> dict:
        self.system.confirm()
        released_before_pause = self._released
        self._sync_hvac_context()
        control_context = self._hvac_context
        now = dt_util.now()
        occupied = self._update_presence()

        night_start = _parse_time(self.tune(CONF_NIGHT_START, DEFAULT_NIGHT_START))
        night_end = _parse_time(self.tune(CONF_NIGHT_END, DEFAULT_NIGHT_END))
        is_night = _in_window(now.time(), night_start, night_end)
        is_workday = self._is_workday()
        plan = self._plan()
        if (
            self.manual_mode_until is not None
            and dt_util.utcnow() >= self.manual_mode_until
        ):
            self.manual_mode = MODE_AUTO
            self.manual_mode_until = None

        # Night away-latch (Nest/ecobee style): only allow Away overnight if the
        # house was already empty/away at the moment night began. Once anyone is
        # present during the night, latch it off for the rest of the night.
        window_start = now.replace(hour=night_start.hour, minute=night_start.minute, second=night_start.second, microsecond=0)
        if window_start > now:
            window_start -= timedelta(days=1)
        window_start = dt_util.as_utc(window_start) if is_night else None
        if window_start != self._night_window_start:
            self._night_window_start = window_start
            delay = timedelta(minutes=int(self.tune(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY)))
            self._night_away_allowed = bool(
                is_night and not occupied and self._absence_since
                and self._absence_since + delay <= window_start
            )
        if is_night and occupied:
            self._night_away_allowed = False

        if self._prearrival_until and (not self._prearrival_active() or occupied):
            self.clear_prearrival()

        if self.windows.busy:
            restore = self.windows.restore
            released = restore.get("released", True) if restore else released_before_pause
            if await self.windows.async_step(released):
                self.clear_prearrival()
                self.clear_manual_hold()
                return self._state("windows_open", "windows_open" if self.windows.active else "windows_restoring", None, plan.tier_at(now, is_workday), occupied, is_night, rate_plan=plan_to_dict(plan))
            self._sync_hvac_context()
            control_context = self._hvac_context
            self._released = released
            plan = self._plan()

        if self.system.pending:
            return self._state("inactive", "system_mode_pending", None, plan.tier_at(now, is_workday), occupied, is_night, rate_plan=plan_to_dict(plan))

        if not self.enabled:
            # Hand the thermostat cleanly back to its native schedule (once per
            # disable): otherwise our last hold persists ("until you change it")
            # and silently blocks the ecobee's own comfort schedule.
            if not self._released and (self._release_retry_at is None or dt_util.utcnow() >= self._release_retry_at):
                self.clear_manual_hold()
                self._last_commanded = None  # fresh baseline when re-enabled
                self._released = await self._release_control()
            return self._state(MODE_DISABLED, "disabled", None, None, occupied, is_night)
        if blocked := self._control_blocked():
            self.clear_prearrival()
            if self._release_retry_at is None:
                self.clear_manual_hold()
            if blocked == "heating_not_enabled" and not self._released:
                if self._release_retry_at is None or dt_util.utcnow() >= self._release_retry_at:
                    self._released = await self._release_control()
            return self._state("inactive", blocked, None, plan.tier_at(now, is_workday), occupied, is_night, rate_plan=plan_to_dict(plan))
        self._release_retry_at = None
        self._released = False

        comfort_home = float(self.tune(CONF_COMFORT_HOME, DEFAULT_COMFORT_HOME))
        away_temp = float(self.tune(CONF_AWAY_TEMP, DEFAULT_AWAY_TEMP))
        vacation_temp = float(self.tune(CONF_VACATION_TEMP, DEFAULT_VACATION_TEMP))
        if self._hvac_mode() == "heat":
            comfort_home = float(self.tune(CONF_HEAT_HOME, DEFAULT_HEAT_HOME))
            away_temp = float(self.tune(CONF_HEAT_AWAY, DEFAULT_HEAT_AWAY))
            vacation_temp = float(self.tune(CONF_HEAT_VACATION, DEFAULT_HEAT_VACATION))
        forced = self.manual_mode if self.manual_mode in (
            MODE_HOME, MODE_AWAY, MODE_SLEEP, MODE_VACATION
        ) else None

        # Manual-hold respect (auto mode only): a hand adjustment on the
        # thermostat is honored until the next night-window transition, like
        # ecobee's "hold until next transition". Explicit select choices,
        # Resume, vacation/away/pre-arrival all supersede it.
        actual = self._thermostat_actual()
        self._confirm_pending()
        if self._manual_hold_until and dt_util.utcnow() >= self._manual_hold_until:
            self.clear_manual_hold()
        if forced is None and not self.vacation:
            grace_over = self._pending_command is None and self._last_commanded is not None and (
                self._commanded_at is None
                or dt_util.utcnow() - self._commanded_at > _MANUAL_GRACE
            )
            if (
                self._manual_hold_until is None
                and grace_over
                and actual is not None
                and not self._matches_command(actual)
            ):
                self._manual_hold = actual
                self._manual_hold_until = self._next_boundary(now, night_start, night_end)
                _LOGGER.info(
                    "Climado: manual adjustment detected (%s); respecting until %s",
                    actual,
                    self._manual_hold_until,
                )
            elif self._manual_hold_until is not None and actual is not None:
                self._manual_hold = actual  # user re-adjusted during the hold

        tier = plan.tier_at(now, is_workday)

        if self.vacation or forced == MODE_VACATION:
            mode, action, reason = MODE_VACATION, ("temp", vacation_temp), "vacation"
        elif forced == MODE_AWAY:
            mode, action, reason = MODE_AWAY, ("temp", away_temp), "manual_away"
        elif self._prearrival_active():
            mode, action = MODE_PREARRIVAL, ("temp", float(self._prearrival_target))
            reason = "pre_arrival_heat" if self._hvac_mode() == "heat" else "pre_arrival"
        elif (
            forced is None
            and not occupied
            and self._away_elapsed()
            and (not is_night or self._night_away_allowed)
        ):
            mode, action, reason = MODE_AWAY, ("temp", away_temp), "away"
        elif forced is None and self._manual_hold_until is not None:
            # Respect the hand adjustment: no writes until the next transition.
            mode, action, reason = MODE_MANUAL_HOLD, ("none", None), "manual_hold"
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
        elif action[0] == "none":
            # Manual hold: report the user's value, write nothing.
            target = self._manual_hold[1] if self._manual_hold and self._manual_hold[0] == "temp" else None
            applied = None
        else:
            target = action[1]
            applied = await self._apply(target)
        if control_context != self._fuel_context():
            # A service awaited above may finish after the user changes modes.
            # Never publish its old target as the new season's resolved target.
            self._sync_hvac_context()
            return self._state("inactive", "thermostat_mode_changed", None, tier, occupied, is_night, rate_plan=plan_to_dict(self._plan()))
        state = self._state(
            mode, reason, target, tier, occupied, is_night, applied, plan_to_dict(plan)
        )
        state["is_workday"] = is_workday
        state["rate_profile"] = "weekday" if is_workday else "weekend"
        state["main_temp"] = self._get_float(self.opt(CONF_MAIN_TEMP_SENSOR))
        state["bedroom_temp"] = self._get_float(self.opt(CONF_BEDROOM_TEMP_SENSOR))
        state["hvac_action"] = self._climate_attr("hvac_action")
        state["thermostat_target"] = self._climate_attr("temperature")
        state["control_temperature"] = self._climate_attr("current_temperature")
        climate = self.hass.states.get(self.opt(CONF_CLIMATE_ENTITY))
        state["thermostat_updated_at"] = climate.last_updated.isoformat() if climate else None
        # Which sensor the thermostat is regulating right now (bedroom during the
        # ecobee Sleep handoff, else the main-floor thermostat sensor).
        state["regulating"] = "bedroom" if mode == MODE_SLEEP else "main"
        state["next_transition"] = self._next_transition(
            now, night_start, night_end, is_night, plan, is_workday
        )
        return state

    async def _apply(self, target):
        if target is None:
            return None
        ent = self.opt(CONF_CLIMATE_ENTITY)
        state = self.hass.states.get(ent)
        if state is None:
            _LOGGER.warning("Climado: climate entity %s not found", ent)
            return None
        if self._control_blocked():
            return None
        heating = state.state == "heat"
        dmin = float(state.attributes.get("min_temp", 10 if heating else DEVICE_COOL_MIN))
        dmax = float(state.attributes.get("max_temp", 28 if heating else DEVICE_COOL_MAX))
        step = float(state.attributes.get("target_temp_step", 0.5) or 0.5)
        if not all(math.isfinite(v) for v in (float(target), dmin, dmax, step)) or step <= 0 or dmin > dmax:
            self._pending_command = None
            self._retry_at = None
            self._command_error = "Invalid thermostat target or limits"
            return None
        value = min(dmax, max(dmin, float(target)))
        value = min(dmax, max(dmin, round(value / step) * step))
        current = state.attributes.get("temperature")
        command = ("temp", value)
        if self._commands_match(command, self._thermostat_actual()) and self._pending_command in (None, command):
            self._record_command(("temp", value), wrote=False)
            return current
        return self._climate_attr("temperature") if await self._send_command(command) else None

    async def _release_control(self):
        """Cancel our hold so the thermostat's native schedule resumes."""
        ent = self.opt(CONF_CLIMATE_ENTITY)
        if self.hass.services.has_service("ecobee", "resume_program"):
            try:
                await self.hass.services.async_call(
                    "ecobee",
                    "resume_program",
                    {"entity_id": ent, "resume_all": True},
                    blocking=True,
                )
                _LOGGER.info("Climado disabled: resumed %s native program", ent)
                self._release_retry_at = None
                self._command_error = None
                return True
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Climado: failed to resume program on %s: %s", ent, err)
                self._command_error = str(err) or type(err).__name__
                self._release_retry_at = dt_util.utcnow() + _RETRY_DELAY
                return False
        else:
            _LOGGER.info(
                "Climado disabled: no resume service for %s; last hold remains", ent
            )
        return True

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
        if self._control_blocked():
            return None
        command = ("preset", preset)
        if state.attributes.get("preset_mode") == preset and self._pending_command in (None, command):
            self._record_command(("preset", preset), wrote=False)
            return state.attributes.get("temperature")  # already active; avoid churn
        return self._climate_attr("temperature") if await self._send_command(command) else None

    def _state(self, mode, reason, target, tier, occupied, is_night, applied=None, rate_plan=None) -> dict:
        return {
            "system_control": self.system.snapshot(),
            "hvac_mode": self._hvac_mode(),
            "heating_enabled": self.opt(CONF_HEATING_ENABLED, False),
            "fuel_control": "manual",
            "aux_heat": self._aux_selection(),
            "aux_heat_entity": self._aux_entity,
            "fuel_discovery_error": self._fuel_discovery_error,
            "selected_source": selected_source(self._hvac_mode(), self._aux_selection()),
            "running_source": running_source(self._climate_attr("equipment_running"), self._climate_attr("hvac_action")),
            "equipment_running": self._climate_attr("equipment_running"),
            "hvac_action": self._climate_attr("hvac_action"),
            "thermostat_target": self._climate_attr("temperature"),
            "control_temperature": self._climate_attr("current_temperature"),
            "main_temp": self._get_float(self.opt(CONF_MAIN_TEMP_SENSOR)),
            "bedroom_temp": self._get_float(self.opt(CONF_BEDROOM_TEMP_SENSOR)),
            "is_workday": self._is_workday(),
            "rate_profile": "weekday" if self._is_workday() else "weekend",
            "mode": mode,
            "windows_open": self.windows.active,
            "windows_restoring": not self.windows.active and self.windows.restore is not None,
            "windows_error": self.windows.error,
            "windows_pending": self.windows.retry_at is not None,
            "reason": reason,
            "target": target,
            "applied": applied,
            "command_pending": self._pending_command is not None,
            "command_error": self._command_error,
            "tier": tier.name if tier is not None else None,
            "tier_id": tier.tier_id if tier is not None else None,
            "rate_plan": rate_plan,
            "occupied": occupied,
            "is_night": is_night,
            "night_away_allowed": self._night_away_allowed,
            "vacation": self.vacation,
            "enabled": self.enabled,
            "manual_mode": self.manual_mode,
            "manual_mode_until": self.manual_mode_until.isoformat()
            if self.manual_mode_until
            else None,
            "prearrival_active": self._prearrival_active(),
            "prearrival_until": self._prearrival_until.isoformat()
            if self._prearrival_until
            else None,
            "manual_hold_active": self._manual_hold_until is not None,
            "manual_hold_until": self._manual_hold_until.isoformat()
            if self._manual_hold_until
            else None,
            "manual_hold_value": list(self._manual_hold) if self._manual_hold else None,
        }
