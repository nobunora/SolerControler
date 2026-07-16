from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TieredTariff:
    tier1_upper_kwh: float
    tier2_upper_kwh: float
    tier1_rate_yen: float
    tier2_rate_yen: float
    tier3_rate_yen: float

    def total_cost(self, day_kwh: float) -> float:
        kwh = max(0.0, float(day_kwh))
        tier1 = max(0.0, float(self.tier1_upper_kwh))
        tier2 = max(tier1, float(self.tier2_upper_kwh))
        band1 = min(kwh, tier1)
        band2 = min(max(kwh - tier1, 0.0), tier2 - tier1)
        band3 = max(kwh - tier2, 0.0)
        return (
            band1 * self.tier1_rate_yen
            + band2 * self.tier2_rate_yen
            + band3 * self.tier3_rate_yen
        )

    def incremental_cost(self, before_kwh: float, added_kwh: float) -> float:
        before = max(0.0, float(before_kwh))
        added = max(0.0, float(added_kwh))
        return self.total_cost(before + added) - self.total_cost(before)


def tiered_day_cost(
    day_kwh: float,
    *,
    tier1_upper_kwh: float,
    tier2_upper_kwh: float,
    rate_tier1_yen: float,
    rate_tier2_yen: float,
    rate_tier3_yen: float,
) -> float:
    return TieredTariff(
        tier1_upper_kwh=tier1_upper_kwh,
        tier2_upper_kwh=tier2_upper_kwh,
        tier1_rate_yen=rate_tier1_yen,
        tier2_rate_yen=rate_tier2_yen,
        tier3_rate_yen=rate_tier3_yen,
    ).total_cost(day_kwh)


def tiered_day_increment_cost(
    *,
    previous_kwh: float,
    delta_kwh: float,
    tier1_upper_kwh: float,
    tier2_upper_kwh: float,
    rate_tier1_yen: float,
    rate_tier2_yen: float,
    rate_tier3_yen: float,
) -> float:
    return TieredTariff(
        tier1_upper_kwh=tier1_upper_kwh,
        tier2_upper_kwh=tier2_upper_kwh,
        tier1_rate_yen=rate_tier1_yen,
        tier2_rate_yen=rate_tier2_yen,
        tier3_rate_yen=rate_tier3_yen,
    ).incremental_cost(previous_kwh, delta_kwh)

