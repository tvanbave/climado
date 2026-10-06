"""Read-only Ecobee fuel discovery and equipment status."""
from homeassistant.helpers import entity_registry as er


def find_aux_entity(hass, climate_entity):
    registry = er.async_get(hass)
    climate = registry.async_get(climate_entity)
    if not climate or not climate.device_id:
        return None
    matches = [
        entry.entity_id for entry in er.async_entries_for_device(registry, climate.device_id)
        if entry.domain == "switch" and entry.platform == "ecobee"
        and entry.unique_id.endswith("_aux_heat_only") and entry.disabled_by is None
    ]
    if len(matches) > 1:
        raise ValueError("Cannot identify a unique auxiliary heat switch")
    return matches[0] if matches else None


def aux_selection(hass, climate_entity, aux_entity=None):
    """A configured/discovered switch is authoritative, including unavailable."""
    if aux_entity:
        state = hass.states.get(aux_entity)
        return {"on": True, "off": False}.get(state.state) if state else None
    state = hass.states.get(climate_entity)
    value = state.attributes.get("aux_heat") if state else None
    if value is True or value == "on":
        return True
    if value is False or value == "off":
        return False
    return None


def selected_source(mode, aux):
    if mode == "off":
        return "off"
    if mode == "cool":
        return "cooling"
    if mode == "heat" and aux is not None:
        return "gas" if aux else "heat_pump"
    return "unknown"


def running_source(equipment, hvac_action):
    """Decode observed equipment only; selected mode never proves operation."""
    if not isinstance(equipment, str):
        return "unknown"
    tokens = {token.strip() for token in equipment.split(",") if token.strip()}
    pump = bool(tokens & {"heatPump", "heatPump2", "heatPump3"})
    gas = bool(tokens & {"auxHeat1", "auxHeat2", "auxHeat3"})
    if pump and gas:
        return "mixed"
    known = {"heatPump", "heatPump2", "heatPump3", "auxHeat1", "auxHeat2", "auxHeat3", "compCool1", "compCool2", "fan"}
    if tokens - known:
        return "unknown"
    if pump:
        return "heat_pump"
    if gas:
        return "gas"
    if tokens & {"compCool1", "compCool2"}:
        return "cooling"
    if tokens == {"fan"}:
        return "fan"
    return "idle" if not tokens and hvac_action in ("idle", "off") else "unknown"
