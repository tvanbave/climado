"""TOU/ULO rate engine for Climado.

Generalized model: an ordered set of price *tiers* (each with a relative
``rank``), a weekly time->tier schedule with separate weekday and
weekend/holiday profiles, a per-tier *coast* allowance (let the house drift in
the comfort-degrading direction during expensive tiers) and a per-tier
*pre-condition* (pre-cool/pre-heat before entering a more-expensive tier).

For M1 the schedule is the Ontario ULO layout; only the on-peak coast and
pre-cool knobs are user-tunable. M2/M3 generalize to a fully editable plan.

All offsets here are expressed in COOLING orientation:
    positive offset  => warmer target (coast)
    negative offset  => cooler target (pre-cool)
A heating implementation later flips the sign.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class Tier:
    """A single price tier."""

    tier_id: str
    name: str
    rank: int  # higher == more expensive
    coast_offset: float = 0.0  # magnitude; warmer when cooling
    precool_lead: int = 0  # minutes before this tier starts
    precool_depth: float = 0.0  # magnitude; cooler when cooling


@dataclass(frozen=True)
class RatePlan:
    """A weekly rate plan."""

    tiers: dict[str, Tier]
    weekday: list[tuple[time, time, str]]  # (start, end-exclusive, tier_id)
    weekend: list[tuple[time, time, str]]

    def _blocks(self, is_workday: bool) -> list[tuple[time, time, str]]:
        return self.weekday if is_workday else self.weekend

    def tier_at(self, when: datetime, is_workday: bool) -> Tier:
        """Return the active tier at ``when``."""
        t = when.time()
        for start, end, tier_id in self._blocks(is_workday):
            if start <= t < end:
                return self.tiers[tier_id]
        # Fallback to the last block (covers the 23:59:59 boundary edge).
        return self.tiers[self._blocks(is_workday)[-1][2]]

    def next_higher_boundary(self, when: datetime, is_workday: bool):
        """Next upcoming block today whose tier ranks higher than the current.

        Returns ``(tier, seconds_until_start)`` or ``None``.
        """
        current = self.tier_at(when, is_workday)
        t = when.time()
        for start, end, tier_id in self._blocks(is_workday):
            if start > t:
                tier = self.tiers[tier_id]
                if tier.rank > current.rank:
                    start_dt = when.replace(
                        hour=start.hour, minute=start.minute, second=0, microsecond=0
                    )
                    return tier, (start_dt - when).total_seconds()
        return None


def rate_offset(plan: RatePlan, when: datetime, is_workday: bool) -> tuple[float, str]:
    """Resolve the cooling-oriented offset and a human reason at ``when``.

    Pre-condition (cooling deeper) takes precedence over the current tier's
    coast when we are inside the lead window before a more-expensive tier.
    """
    current = plan.tier_at(when, is_workday)
    nb = plan.next_higher_boundary(when, is_workday)
    if nb is not None:
        tier, seconds = nb
        if tier.precool_lead > 0 and 0 <= seconds <= tier.precool_lead * 60:
            return -abs(tier.precool_depth), f"precool:{tier.tier_id}"
    if current.coast_offset:
        return abs(current.coast_offset), f"coast:{current.tier_id}"
    return 0.0, f"tier:{current.tier_id}"


def default_ulo_plan(
    onpeak_coast: float, precool_lead: int, precool_depth: float
) -> RatePlan:
    """Ontario ULO preset with user-tunable on-peak coast / pre-cool."""
    tiers = {
        "ultra_low": Tier("ultra_low", "Ultra-low overnight", 0),
        "off_peak": Tier("off_peak", "Off-peak (weekend/holiday)", 1),
        "mid_peak": Tier("mid_peak", "Mid-peak", 2),
        "on_peak": Tier(
            "on_peak",
            "On-peak",
            3,
            coast_offset=onpeak_coast,
            precool_lead=precool_lead,
            precool_depth=precool_depth,
        ),
    }
    eod = time(23, 59, 59)
    weekday = [
        (time(0, 0), time(7, 0), "ultra_low"),
        (time(7, 0), time(16, 0), "mid_peak"),
        (time(16, 0), time(21, 0), "on_peak"),
        (time(21, 0), time(23, 0), "mid_peak"),
        (time(23, 0), eod, "ultra_low"),
    ]
    weekend = [
        (time(0, 0), time(7, 0), "ultra_low"),
        (time(7, 0), time(23, 0), "off_peak"),
        (time(23, 0), eod, "ultra_low"),
    ]
    return RatePlan(tiers=tiers, weekday=weekday, weekend=weekend)
