"""Synthetic performance curves: not specifications for the user's heat pump."""
from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.climado.fuel_cost import (
    UNION_SOUTH_M1_2026_Q3 as TARIFF,
)
from custom_components.climado.fuel_cost import (
    GasTariff,
    compare_heat_costs,
    interpolate_cop,
)

DAY = date(2026, 9, 6)
NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def estimate(**overrides):
    return compare_heat_costs(**{
        "now": NOW, "outdoor_reported_at": NOW, "max_age": timedelta(minutes=30),
        "outdoor_c": 0, "cop_points": ((-20, 1), (0, 3), (20, 5)),
        "electricity_per_kwh": 0.1, "gas_tariff": TARIFF, "gas_used_m3": 300,
        "gas_kwh_per_m3": 10.5, "furnace_efficiency": 0.962,
        "furnace_aux_kwh_per_heat_kwh": 0, **overrides,
    })


def test_gas_blocks_and_unknown_consumption():
    assert TARIFF.marginal_range(DAY, 0) == (0.261177, 0.261177)
    assert TARIFF.marginal_range(DAY, 100) == (0.257632, 0.257632)
    assert TARIFF.marginal_range(DAY, 250) == (0.248480, 0.248480)
    assert TARIFF.marginal_range(DAY, None) == (0.248480, 0.261177)
    assert TARIFF.incremental_cost(DAY, 90, 180) == pytest.approx(10 * .261177 + 150 * .257632 + 20 * .248480)
    assert TARIFF.incremental_cost(DAY, 0, 0) == 0  # no fixed monthly charges


def test_cop_interpolation():
    assert interpolate_cop(((-10, 2), (10, 4)), 0) == 3
    assert interpolate_cop(((-10, 2), (10, 4)), -10) == 2
    assert interpolate_cop(((-10, 2), (10, 4)), 10) == 4


def test_electricity_tier_changes_winner():
    assert estimate(electricity_per_kwh=.04).preferred_source == "heat_pump"
    assert estimate(electricity_per_kwh=.25).preferred_source == "gas"


def test_outdoor_temperature_changes_winner():
    assert estimate(outdoor_c=-20, electricity_per_kwh=.06).preferred_source == "gas"
    assert estimate(outdoor_c=10, electricity_per_kwh=.06).preferred_source == "heat_pump"


def test_costs_and_unknown_usage_range():
    result = estimate(gas_used_m3=None, furnace_aux_kwh_per_heat_kwh=.02)
    assert result.heat_pump_per_kwh == pytest.approx(.1 / 3)
    assert result.furnace_per_kwh_min == pytest.approx(.248480 / (10.5 * .962) + .002)
    assert result.furnace_per_kwh_max > result.furnace_per_kwh_min


def test_tie_and_overlap_do_not_recommend_switch():
    price = .248480 / (10.5 * .962) * 3
    assert estimate(electricity_per_kwh=price).preferred_source is None
    assert estimate(gas_used_m3=None, electricity_per_kwh=.075).preferred_source is None


@pytest.mark.parametrize("overrides", [
    {"outdoor_c": -21}, {"outdoor_c": float("nan")},
    {"cop_points": ((0, 3), (0, 4))}, {"cop_points": ((0, 0), (10, 3))},
    {"cop_points": ((0, 3),)}, {"electricity_per_kwh": float("inf")},
    {"gas_kwh_per_m3": 0}, {"furnace_efficiency": 96.2},
    {"furnace_efficiency": 0}, {"savings_margin": 1},
    {"gas_used_m3": -1}, {"furnace_aux_kwh_per_heat_kwh": -1},
    {"outdoor_reported_at": NOW - timedelta(minutes=30)},
    {"outdoor_reported_at": NOW + timedelta(minutes=1)},
    {"outdoor_reported_at": NOW.replace(tzinfo=None)},
    {"now": NOW.replace(month=10), "outdoor_reported_at": NOW.replace(month=10)},
])
def test_invalid_or_stale_inputs_reject_estimate(overrides):
    with pytest.raises(ValueError):
        estimate(**overrides)


@pytest.mark.parametrize("blocks", [((100, .2),), ((100, .2), (50, .3), (None, .4)), ((None, .2), (None, .3))])
def test_invalid_tariff_blocks(blocks):
    with pytest.raises(ValueError):
        GasTariff(date(2026, 7, 1), date(2026, 10, 1), blocks, "test")
