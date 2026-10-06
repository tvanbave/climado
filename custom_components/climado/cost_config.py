"""Native form selectors for the optional heat-cost advisory."""
import voluptuous as vol
from homeassistant.helpers import selector


def _number(low, high, step=0.001, unit=None):
    config = {"min": low, "max": high, "step": "any" if step < 0.001 else step, "mode": "box"}
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _field(label, control, required=True):
    return {"label": label, "selector": control, "required": required}


def cost_schema(current):
    controls = {
        "enabled": selector.BooleanSelector(),
        "electricity_prices": selector.ObjectSelector({"fields": {
            tier: _field(label, _number(0, 5, 0.00001, "CAD/kWh")) for tier, label in (
                ("ultra_low", "Ultra-low"), ("off_peak", "Off-peak"),
                ("mid_peak", "Mid-peak"), ("on_peak", "On-peak"),
            )
        }}),
        "electricity_valid_from": selector.DateSelector(),
        "electricity_valid_until": selector.DateSelector(),
        "electricity_source": selector.TextSelector(),
        "gas_blocks": selector.ObjectSelector({"multiple": True, "label_field": "price", "fields": {
            "upper_m3": _field("Cumulative billing-period limit (omit for final block)", _number(1, 100000, 1, "m³"), False),
            "price": _field("Variable gas price", _number(0.000001, 10, 0.000001, "CAD/m³")),
        }}),
        "gas_valid_from": selector.DateSelector(),
        "gas_valid_until": selector.DateSelector(),
        "gas_source": selector.TextSelector(),
        "gas_usage_sensor": selector.EntitySelector({"domain": "sensor"}),
        "gas_kwh_per_m3": _number(0.01, 30, 0.001, "kWh/m³"),
        "furnace_efficiency": _number(0.01, 1, 0.001),
        "furnace_aux_kwh_per_heat_kwh": _number(0, 1, 0.001),
        "cop_points": selector.ObjectSelector({"multiple": True, "label_field": "outdoor_c", "description_field": "cop", "fields": {
            "outdoor_c": _field("Outdoor temperature", _number(-100, 100, 0.1, "°C")),
            "cop": _field("Matched-system COP", _number(0.01, 20, 0.01)),
        }}),
        "cop_source": selector.TextSelector(),
        "outdoor_max_age_minutes": _number(1, 120, 1, "min"),
        "savings_margin": _number(0, 0.5, 0.01),
        "prices_consistent": selector.BooleanSelector(),
    }
    defaults = {"enabled": False, "prices_consistent": False, "outdoor_max_age_minutes": 30, "savings_margin": 0.05, **current}
    return vol.Schema({vol.Optional(key, default=defaults.get(key, vol.UNDEFINED)): control for key, control in controls.items()})
