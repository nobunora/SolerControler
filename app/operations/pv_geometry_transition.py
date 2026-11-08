"""Recognize only the panel correction explicitly approved on 2026-11-08."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Any

from app.forecasting.clear_ghi_observations import site_fingerprint
from app.forecasting.pv_array import load_pv_array_configs
from app.forecasting.pv_ideal_max import BASIS_VERSION, MODEL_ID, IdealMaxArray


def approved_panel_geometry_transition(new: dict[str, Any], old: dict[str, Any]) -> bool:
    """No arbitrary site alias or caller-supplied migration permission is accepted."""
    from app.operations.pv_ideal_max import configured_ideal_max_geometry

    try:
        arrays = load_pv_array_configs()
        if (len(arrays) != 3 or {a.azimuth_deg for a in arrays} != {-90., 0., 90.}
                or any(a.capacity_kw != 1.08 or a.tilt_deg != 19. for a in arrays)):
            return False
        target = configured_ideal_max_geometry()
        source_arrays = [replace(a, capacity_kw=1., tilt_deg=20.) for a in arrays]
        source = replace(target, arrays=tuple(IdealMaxArray(a.azimuth_deg, a.tilt_deg, a.capacity_kw,
                         a.performance_ratio, a.shading_factor) for a in source_arrays))
        for trace, geometry, layout in ((old, source, source_arrays), (new, target, arrays)):
            pack = trace.get("calibration_pack")
            if (trace.get("model_id") != "physical_pv_ideal_max_global_v1"
                    or trace.get("basis_version") != BASIS_VERSION or not isinstance(pack, dict)
                    or pack.get("model_id") != MODEL_ID or pack.get("basis_version") != BASIS_VERSION
                    or pack.get("geometry_fingerprint") != geometry.fingerprint
                    or trace.get("site_fingerprint") != site_fingerprint(latitude=geometry.latitude,
                        longitude=geometry.longitude, timezone_name=geometry.timezone_name,
                        arrays=[asdict(a) for a in layout])):
                return False
            raw = json.dumps(pack, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            if trace.get("calibration_sha256") != hashlib.sha256(raw).hexdigest():
                return False
        return True
    except (ValueError, KeyError, TypeError, OSError):
        return False
