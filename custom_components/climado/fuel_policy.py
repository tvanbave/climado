"""Pure fuel-selection policy for simulation; not connected to device services."""
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class FuelDecision:
    source: str | None
    switch: bool
    reason: str


def decide_fuel(*, current, preferred, eligible, now: datetime,
                last_changed: datetime | None, minimum_dwell: timedelta,
                enabled=False, paused=False, manual_override=False,
                inputs_valid=False, transition_pending=False, required_source=None):
    """Eligibility and comfort are supplied by a future verified equipment adapter."""
    if now.utcoffset() is None or minimum_dwell < timedelta(0):
        raise ValueError("Policy requires aware time and a non-negative dwell")
    if not set(eligible) <= {"heat_pump", "gas"}:
        raise ValueError("Unknown eligible fuel")
    for blocked, reason in (
        (not enabled, "automation_disabled"), (paused, "windows_open"),
        (manual_override, "manual_override"), (transition_pending, "transition_pending"),
        (not inputs_valid, "inputs_unavailable"),
        (current not in ("heat_pump", "gas"), "selection_unknown"),
    ):
        if blocked:
            return FuelDecision(current, False, reason)
    desired = required_source or preferred
    if desired is None:
        return FuelDecision(current, False, "within_savings_margin")
    if desired not in eligible:
        return FuelDecision(current, False, "source_ineligible")
    if desired == current:
        return FuelDecision(current, False, "retain_source")
    if last_changed is None:
        return FuelDecision(current, False, "dwell_unknown")
    if last_changed.utcoffset() is None or last_changed > now:
        raise ValueError("Invalid last confirmed fuel change")
    if now - last_changed < minimum_dwell:
        return FuelDecision(current, False, "minimum_dwell")
    return FuelDecision(desired, True, "comfort_required" if required_source else "lower_cost")
