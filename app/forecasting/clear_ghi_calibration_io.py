"""Single-writer local completed originals; shared-store CAS belongs to adapters."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from app.forecasting.pv_clear_ghi import (
    ClearGhiCalibration,
    calibration_from_bytes,
    canonical_pack_bytes,
    validate_calibration_for_run,
)

LOGGER = logging.getLogger(__name__)


def load_clear_ghi_calibration(
    path: Path, *, expected_site: str, expected_basis: str, target_date: date, as_of: datetime,
) -> ClearGhiCalibration:
    """Read exactly the selected hash-named original, without fallback discovery."""
    path = Path(path)
    if path.suffix != ".json" or re.fullmatch(r"[0-9a-f]{64}", path.stem) is None:
        raise ValueError("calibration original: hash-named path required")
    calibration = calibration_from_bytes(path.read_bytes(), sha256=path.stem)
    validate_calibration_for_run(calibration, expected_site=expected_site,
                                 expected_basis=expected_basis, target_date=target_date, as_of=as_of)
    return calibration


def _current_calibration(directory: Path) -> ClearGhiCalibration | None:
    reference = directory / "current.json"
    if not reference.exists():
        return None
    raw = reference.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"pack_sha256"}:
        raise ValueError("calibration reference: invalid schema")
    if canonical_pack_bytes(data) != raw:
        raise ValueError("calibration reference: noncanonical bytes")
    sha256 = data["pack_sha256"]
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise ValueError("calibration reference: invalid hash")
    return calibration_from_bytes((directory / f"{sha256}.json").read_bytes(), sha256=sha256)


def _write_temp(directory: Path, raw_bytes: bytes) -> Path:
    descriptor, filename = tempfile.mkstemp(prefix=".clear-ghi-", suffix=".tmp", dir=directory)
    path = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw_bytes)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def write_clear_ghi_calibration(calibration: ClearGhiCalibration, *, directory: Path) -> Path:
    """Create/verify an immutable original before replacing a local current reference.

    Callers must serialize writes. This is not a substitute for GCS generation CAS.
    A failed update leaves the previous reference unchanged and raises to its owner.
    """
    calibration_from_bytes(calibration.raw_bytes, sha256=calibration.sha256)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    current = _current_calibration(directory)
    if current is not None:
        if (current.site_fingerprint, current.basis_version) != (calibration.site_fingerprint, calibration.basis_version):
            raise ValueError("calibration reference: site/basis mismatch")
        candidate_as_of = calibration.training_as_of.astimezone(timezone.utc)
        current_as_of = current.training_as_of.astimezone(timezone.utc)
        if (candidate_as_of < current_as_of
                or (candidate_as_of == current_as_of and calibration.sha256 != current.sha256)):
            LOGGER.warning("pv_clear_ghi_calibration_rejected stage=local_reference reason=training_generation sha256=%s", calibration.sha256)
            raise ValueError("calibration reference: older/conflicting training generation")
    original = directory / f"{calibration.sha256}.json"
    temporary: Path | None = None
    try:
        if not original.exists():
            temporary = _write_temp(directory, calibration.raw_bytes)
            try:
                os.link(temporary, original)
            except FileExistsError:
                pass
            temporary.unlink(missing_ok=True)
            temporary = None
        verified = calibration_from_bytes(original.read_bytes(), sha256=calibration.sha256)
        if verified.raw_bytes != calibration.raw_bytes:
            raise ValueError("calibration original: read-back mismatch")
        if current is not None and current.sha256 == verified.sha256:
            return original
        temporary = _write_temp(directory, canonical_pack_bytes({"pack_sha256": verified.sha256}))
        os.replace(temporary, directory / "current.json")
        temporary = None
        return original
    except (OSError, ValueError, TypeError):
        LOGGER.warning("pv_clear_ghi_calibration_rejected stage=local_write reason=update_failed sha256=%s", calibration.sha256)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
