"""Compatibility exports for the night-plan domain model.

New code should import from :mod:`app.energy_plan.night_plan`.
"""

from app.energy_plan.night_plan import NightPlan, NightPlanResult, parse_night_plan, read_night_plan

__all__ = ["NightPlan", "NightPlanResult", "parse_night_plan", "read_night_plan"]
