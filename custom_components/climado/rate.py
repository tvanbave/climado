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


KNOWN_TIERS = ("ultra_low", "off_peak", "mid_peak", "on_peak")
_TIER_META = {
    "ultra_low": ("Ultra-low overnight", 0),
    "off_peak": ("Off-peak (weekend/holiday)", 1),
    "mid_peak": ("Mid-peak", 2),
    "on_peak": ("On-peak", 3),
}
_EOD = time(23, 59, 59)


def _tiers(onpeak_coast: float, precool_lead: int, precool_depth: float) -> dict[str, Tier]:
    """The standard four tiers; on-peak carries the tunable coast/pre-cool."""
    out: dict[str, Tier] = {}
    for tid, (name, rank) in _TIER_META.items():
        if tid == "on_peak":
            out[tid] = Tier(
                tid, name, rank,
                coast_offset=onpeak_coast,
                precool_lead=precool_lead,
                precool_depth=precool_depth,
            )
        else:
            out[tid] = Tier(tid, name, rank)
    return out


def _hour_to_time(h) -> time:
    h = int(h)
    return _EOD if h >= 24 else time(h, 0)


def _time_to_hour(t: time) -> int:
    return 24 if (t.hour == 23 and t.minute >= 59) else t.hour


def normalize_schedule(rows) -> list[list]:
    """Validate [[start_hour, end_hour, tier_id], ...]; raise ValueError if bad.

    Returns the blocks sorted chronologically and requires full, gap-free
    coverage of 00-24 — ``tier_at``/``next_higher_boundary`` assume ordered,
    contiguous blocks.
    """
    out: list[list] = []
    for row in rows:
        if len(row) != 3:
            raise ValueError(f"rate block must be [start, end, tier]: {row!r}")
        start, end, tid = int(row[0]), int(row[1]), str(row[2])
        if tid not in KNOWN_TIERS:
            raise ValueError(f"unknown tier {tid!r}")
        if not (0 <= start < end <= 24):
            raise ValueError(f"invalid hours {start}-{end}")
        out.append([start, end, tid])
    if not out:
        raise ValueError("empty schedule")
    out.sort(key=lambda r: r[0])
    if out[0][0] != 0 or out[-1][1] != 24:
        raise ValueError("schedule must cover 00:00-24:00")
    for prev, nxt in zip(out, out[1:]):
        if nxt[0] != prev[1]:
            raise ValueError(f"schedule gap/overlap at hour {nxt[0]}")
    return out


def plan_from_schedule(weekday, weekend, onpeak_coast, precool_lead, precool_depth) -> RatePlan:
    """Build a plan from arbitrary hour->tier schedules."""
    blocks = lambda rows: [(_hour_to_time(s), _hour_to_time(e), t) for s, e, t in rows]
    return RatePlan(
        tiers=_tiers(onpeak_coast, precool_lead, precool_depth),
        weekday=blocks(weekday),
        weekend=blocks(weekend),
    )


def plan_to_dict(plan: RatePlan) -> dict:
    """Serialize a plan's schedules back to hour-int blocks (for the UI)."""
    rows = lambda blocks: [[_time_to_hour(s), _time_to_hour(e), t] for s, e, t in blocks]
    return {"weekday": rows(plan.weekday), "weekend": rows(plan.weekend)}


def default_ulo_plan(onpeak_coast: float, precool_lead: int, precool_depth: float) -> RatePlan:
    """Ontario ULO preset with user-tunable on-peak coast / pre-cool."""
    weekday = [
        [0, 7, "ultra_low"],
        [7, 16, "mid_peak"],
        [16, 21, "on_peak"],
        [21, 23, "mid_peak"],
        [23, 24, "ultra_low"],
    ]
    weekend = [
        [0, 7, "ultra_low"],
        [7, 23, "off_peak"],
        [23, 24, "ultra_low"],
    ]
    return plan_from_schedule(weekday, weekend, onpeak_coast, precool_lead, precool_depth)
