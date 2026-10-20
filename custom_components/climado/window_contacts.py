"""Continuous contact timing; never infer closure from missing telemetry."""
from datetime import timedelta

from homeassistant.util import dt as dt_util


class WindowContacts:
    """Track every edge before coordinator refreshes can debounce it away."""

    def __init__(self):
        self.states = {}
        self.open_since = {}
        self.closed_since = None
        self.deadline = None

    def observe(self, entity_id, state, now=None):
        now = now or dt_util.utcnow()
        state = state if state in ("on", "off") else "unknown"
        if self.states.get(entity_id) == state:
            return
        self.states[entity_id] = state
        self.open_since.pop(entity_id, None)
        if state == "on":
            self.open_since[entity_id] = now
        self.closed_since = (
            now if self.states and all(value == "off" for value in self.states.values())
            else None
        )

    def evaluate(self, hass, entities, active, open_minutes, close_minutes):
        now = dt_util.utcnow()
        entities = set(entities)
        if entities != set(self.states):
            # Configuration changes and restarts must requalify elapsed time.
            self.states = {entity_id: "unknown" for entity_id in entities}
            self.open_since.clear()
            self.closed_since = None
        for entity_id in entities:
            state = hass.states.get(entity_id)
            self.observe(entity_id, state.state if state else None, now)
        opened = sorted(self.open_since)
        unknown = sorted(key for key, value in self.states.items() if value == "unknown")
        self.deadline = None
        if not entities:
            status = "not_configured"
        elif active:
            status = "sensor_unavailable" if unknown else "paused"
            if self.closed_since is not None:
                self.deadline = self.closed_since + timedelta(minutes=close_minutes)
                if now >= self.deadline:
                    active = False
                    self.deadline = None
                    status = "monitoring"
                else:
                    status = "waiting_to_resume"
        elif opened:
            self.deadline = min(self.open_since.values()) + timedelta(minutes=open_minutes)
            if now >= self.deadline:
                active = True
                self.deadline = None
                status = "sensor_unavailable" if unknown else "paused"
            else:
                status = "waiting_to_pause"
        else:
            status = "sensor_unavailable" if unknown else "monitoring"
        return active, {
            "status": status, "open_entities": opened, "unavailable_entities": unknown,
            "deadline": self.deadline.isoformat() if self.deadline else None,
        }
