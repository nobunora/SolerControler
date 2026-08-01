"""Compatibility exports for the physical PV forecast model.

New code should import from :mod:`app.forecasting.pv_physical`.
"""

from app.forecasting.pv_physical import PhysicalPvCandidate, build_physical_pv_candidate

__all__ = ["PhysicalPvCandidate", "build_physical_pv_candidate"]
