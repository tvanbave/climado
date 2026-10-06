"""Advisory delivered-heat costs. No Home Assistant I/O or fuel commands.

Prices must use the same currency and tax basis. COP must describe the matched
system and electrical loads included in its rating. None of these estimates
establish equipment compatibility, available capacity or a safe switching limit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from math import isfinite


def _number(value: float, name: str, *, positive: bool = False) -> float:
    result = float(value)
    if not isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError(f"Invalid {name}")
    return result


@dataclass(frozen=True)
class GasTariff:
    """Incremental CAD/m3 blocks; final upper bound is None (unlimited)."""

    valid_from: date
    valid_until: date  # exclusive
    blocks: tuple[tuple[float | None, float], ...]
    source: str

    def __post_init__(self):
        if self.valid_from >= self.valid_until or not self.blocks or not self.source:
            raise ValueError("Gas tariff needs dates, blocks and provenance")
        previous = 0.0
        for index, (upper, price) in enumerate(self.blocks):
            _number(price, "gas price", positive=True)
            if upper is None:
                if index != len(self.blocks) - 1:
                    raise ValueError("Only the final gas block can be unlimited")
            else:
                _number(upper, "gas block", positive=True)
                if upper <= previous:
                    raise ValueError("Gas block limits must increase")
                previous = upper
        if self.blocks[-1][0] is not None:
            raise ValueError("Final gas block must be unlimited")

    def check_date(self, when: date) -> None:
        if not self.valid_from <= when < self.valid_until:
            raise ValueError("Gas tariff is not valid for this date")

    def marginal_range(self, when: date, used_m3: float | None) -> tuple[float, float]:
        self.check_date(when)
        if used_m3 is None:
            prices = [price for _, price in self.blocks]
            return min(prices), max(prices)
        used = _number(used_m3, "billing-period gas consumption")
        for upper, price in self.blocks:
            if upper is None or used < upper:
                return price, price
        raise AssertionError("Missing unlimited gas block")

    def incremental_cost(self, when: date, used_m3: float, additional_m3: float) -> float:
        """Cost of extra volume within one billing period, crossing blocks."""
        self.check_date(when)
        used = _number(used_m3, "billing-period gas consumption")
        remaining = _number(additional_m3, "additional gas consumption")
        total = 0.0
        for upper, price in self.blocks:
            volume = remaining if upper is None else min(remaining, max(0, upper - used))
            total += volume * price
            used += volume
            remaining -= volume
            if remaining == 0:
                break
        return total


# Historical preset, never silently extended past its published validity.
# Supply + adjustment + block delivery + facility charge, each exactly once.
UNION_SOUTH_M1_2026_Q3 = GasTariff(
    valid_from=date(2026, 7, 1),
    valid_until=date(2026, 10, 1),
    blocks=((100, 0.261177), (250, 0.257632), (None, 0.248480)),
    source="https://www.oeb.ca/sites/default/files/qram-union-20260701-en.pdf",
)


def interpolate_cop(points: tuple[tuple[float, float], ...], outdoor_c: float) -> float:
    """Linear interpolation within supplied data only; no extrapolation."""
    if len(points) < 2 or not isfinite(outdoor_c):
        raise ValueError("COP requires a temperature and at least two data points")
    previous = float("-inf")
    for temperature, cop in points:
        if not isfinite(temperature) or temperature <= previous:
            raise ValueError("COP temperatures must be finite and strictly increasing")
        _number(cop, "COP", positive=True)
        previous = temperature
    if not points[0][0] <= outdoor_c <= points[-1][0]:
        raise ValueError("Outdoor temperature is outside the validated COP range")
    for (low_t, low_cop), (high_t, high_cop) in pairwise(points):
        if outdoor_c <= high_t:
            return low_cop + (high_cop - low_cop) * (outdoor_c - low_t) / (high_t - low_t)
    raise AssertionError("COP interpolation interval missing")


@dataclass(frozen=True)
class HeatCostEstimate:
    outdoor_c: float
    estimated_cop: float
    heat_pump_per_kwh: float
    furnace_per_kwh_min: float
    furnace_per_kwh_max: float
    preferred_source: str | None
    reason: str


def compare_heat_costs(
    *,
    now: datetime,
    outdoor_reported_at: datetime,
    max_age: timedelta,
    outdoor_c: float,
    cop_points: tuple[tuple[float, float], ...],
    electricity_per_kwh: float,
    gas_tariff: GasTariff,
    gas_used_m3: float | None,
    gas_kwh_per_m3: float,
    furnace_efficiency: float,
    furnace_aux_kwh_per_heat_kwh: float,
    savings_margin: float = 0.05,
) -> HeatCostEstimate:
    """Estimate relative costs, not permission to run either heat source.

Unknown billing consumption returns a gas-cost range. Recommend a source only
if it wins across that range by the relative savings margin. Auxiliary furnace
electricity is an explicit input, not silently assumed to be zero.
"""
    if now.utcoffset() is None or outdoor_reported_at.utcoffset() is None:
        raise ValueError("Outdoor observation needs timezone-aware timestamps")
    age = now - outdoor_reported_at
    if max_age <= timedelta(0) or not timedelta(0) <= age < max_age:
        raise ValueError("Outdoor observation is stale or in the future")
    electric = _number(electricity_per_kwh, "electricity price")
    energy = _number(gas_kwh_per_m3, "gas energy conversion", positive=True)
    efficiency = _number(furnace_efficiency, "furnace efficiency", positive=True)
    margin = _number(savings_margin, "savings margin")
    if efficiency > 1 or margin >= 1:
        raise ValueError("Efficiency and savings margin must be fractions")
    aux = _number(furnace_aux_kwh_per_heat_kwh, "furnace auxiliary electricity")
    cop = interpolate_cop(cop_points, outdoor_c)
    gas_low, gas_high = gas_tariff.marginal_range(now.date(), gas_used_m3)
    heat_pump = electric / cop
    furnace_low = gas_low / (energy * efficiency) + aux * electric
    furnace_high = gas_high / (energy * efficiency) + aux * electric
    preferred, reason = None, "costs_overlap_or_within_margin"
    if heat_pump < furnace_low * (1 - margin):
        preferred, reason = "heat_pump", "heat_pump_cheaper"
    elif furnace_high < heat_pump * (1 - margin):
        preferred, reason = "gas", "gas_cheaper"
    return HeatCostEstimate(outdoor_c, cop, heat_pump, furnace_low, furnace_high, preferred, reason)
