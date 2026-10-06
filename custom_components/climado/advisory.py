"""Configured heat-cost estimates. This module never commands equipment."""
from dataclasses import asdict
from datetime import date, timedelta
from math import isfinite

from .fuel_cost import GasTariff, compare_heat_costs, interpolate_cop
from .rate import KNOWN_TIERS

REQUIRED_INPUTS = (
    "electricity_prices", "electricity_valid_from", "electricity_valid_until",
    "electricity_source", "gas_blocks", "gas_valid_from", "gas_valid_until",
    "gas_source", "gas_kwh_per_m3", "furnace_efficiency",
    "furnace_aux_kwh_per_heat_kwh", "cop_points", "cop_source",
)


def _finite(value, label, minimum=0, maximum=None):
    if isinstance(value, bool):
        raise TypeError(f"{label} must be a number")
    number = float(value)
    if not isfinite(number) or number < minimum or (maximum is not None and number > maximum):
        raise ValueError(f"Invalid {label}")
    return number


def _points(config):
    return tuple((float(row["outdoor_c"]), float(row["cop"])) for row in config["cop_points"])


def _tariff(config):
    return GasTariff(
        date.fromisoformat(config["gas_valid_from"]),
        date.fromisoformat(config["gas_valid_until"]),
        tuple((row.get("upper_m3"), float(row["price"])) for row in config["gas_blocks"]),
        config["gas_source"],
    )


def validate_cost_config(config):
    """Allow incomplete setup; reject malformed supplied values before saving."""
    if not isinstance(config, dict):
        raise TypeError("Cost settings must be an object")
    for name in ("electricity_source", "gas_source", "cop_source"):
        if name in config and (not isinstance(config[name], str) or not config[name].strip()):
            raise ValueError(f"{name} must identify the source of the data")
    for name in ("gas", "electricity"):
        start, end = config.get(f"{name}_valid_from"), config.get(f"{name}_valid_until")
        if start:
            date.fromisoformat(start)
        if end:
            date.fromisoformat(end)
        if start and end and date.fromisoformat(start) >= date.fromisoformat(end):
            raise ValueError(f"{name} validity end must follow its start")
    if "electricity_prices" in config:
        prices = config["electricity_prices"]
        if not isinstance(prices, dict) or set(prices) != set(KNOWN_TIERS):
            raise ValueError("Provide electricity prices for all four ULO tiers")
        for value in prices.values():
            _finite(value, "electricity price")
    if "cop_points" in config:
        if not isinstance(config["cop_points"], list) or not all(isinstance(row, dict) for row in config["cop_points"]):
            raise TypeError("COP points must be a list of objects")
        for row in config["cop_points"]:
            _finite(row["outdoor_c"], "COP outdoor temperature", -100, 100)
            _finite(row["cop"], "COP", 0.01, 20)
        points = _points(config)
        interpolate_cop(points, points[0][0] if points else 0)
    if "gas_blocks" in config:
        if not isinstance(config["gas_blocks"], list) or not all(isinstance(row, dict) for row in config["gas_blocks"]):
            raise TypeError("Gas blocks must be a list of objects")
        blocks = tuple((row.get("upper_m3"), _finite(row["price"], "gas price", 0.000001)) for row in config["gas_blocks"])
        GasTariff(date(2000, 1, 1), date(2000, 1, 2), blocks, "validation")
    for key, minimum, maximum in (
        ("gas_kwh_per_m3", 0.01, 30), ("furnace_efficiency", 0.01, 1),
        ("furnace_aux_kwh_per_heat_kwh", 0, 1), ("savings_margin", 0, 0.5),
        ("outdoor_max_age_minutes", 1, 120),
    ):
        if key in config:
            _finite(config[key], key, minimum, maximum)
    return [key for key in REQUIRED_INPUTS if key not in config or config[key] is None]


def heat_advisory(hass, config, outdoor_entity, tier_id, now):
    """Return fresh advisory data, or an explicit reason with no cached costs."""
    base = {"status": "disabled", "reason": "disabled", "preferred_source": None, "tier_id": tier_id}
    if not isinstance(config, dict):
        return {**base, "status": "unavailable", "reason": "invalid_inputs"}
    if not config.get("enabled", False):
        return base
    base.update(status="unavailable", reason="missing_inputs", currency="CAD", automatic_switching=False)
    try:
        missing = validate_cost_config(config)
        if not outdoor_entity:
            missing.append("outdoor_temp_sensor")
        if not config.get("prices_consistent", False):
            missing.append("prices_consistent")
        if missing:
            return {**base, "missing_inputs": missing}
        base["reason"] = "outdoor_unavailable"
        outdoor = hass.states.get(outdoor_entity)
        if outdoor is None or outdoor.state in ("unknown", "unavailable"):
            return base
        if outdoor.attributes.get("unit_of_measurement") != "°C":
            return {**base, "reason": "outdoor_unit"}
        temperature = _finite(outdoor.state, "outdoor temperature", -100, 100)
        reported = outdoor.last_reported
        max_age = timedelta(minutes=float(config.get("outdoor_max_age_minutes", 30)))
        if reported is None or not timedelta(0) <= now - reported < max_age:
            return {**base, "reason": "outdoor_stale"}
        base.update(outdoor_c=temperature, outdoor_reported_at=reported.isoformat())
        if not date.fromisoformat(config["electricity_valid_from"]) <= now.date() < date.fromisoformat(config["electricity_valid_until"]):
            return {**base, "reason": "electricity_tariff_expired"}
        gas = _tariff(config)
        if not gas.valid_from <= now.date() < gas.valid_until:
            return {**base, "reason": "gas_tariff_expired"}
        points = _points(config)
        if not points[0][0] <= temperature <= points[-1][0]:
            return {**base, "reason": "outside_cop_range"}
        used = None
        usage = hass.states.get(config.get("gas_usage_sensor", ""))
        if usage and usage.attributes.get("unit_of_measurement") in ("m³", "m3") and timedelta(0) <= now - usage.last_reported < timedelta(hours=24):
            try:
                used = _finite(usage.state, "gas consumption")
            except (ValueError, TypeError):
                pass  # Unknown consumption widens the gas price range.
        estimate = compare_heat_costs(
            now=now, outdoor_reported_at=reported, max_age=max_age, outdoor_c=temperature,
            cop_points=points, electricity_per_kwh=config["electricity_prices"][tier_id],
            gas_tariff=gas, gas_used_m3=used, gas_kwh_per_m3=config["gas_kwh_per_m3"],
            furnace_efficiency=config["furnace_efficiency"],
            furnace_aux_kwh_per_heat_kwh=config["furnace_aux_kwh_per_heat_kwh"],
            savings_margin=config.get("savings_margin", 0.05),
        )
        return {
            **base, **asdict(estimate), "status": "ready", "gas_usage_known": used is not None,
            "electricity_per_kwh": config["electricity_prices"][tier_id],
            "sources": {key: config[key] for key in ("electricity_source", "gas_source", "cop_source")},
        }
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
        return {**base, "reason": "invalid_inputs", "detail": str(err)}
