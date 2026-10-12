"""Background-only clear-GHI originals and a monotonic current reference."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from google.api_core.exceptions import NotFound, PreconditionFailed

from app.forecasting.clear_ghi_observations import canonical_bytes
from app.forecasting.pv_clear_ghi import (
    BASIS_VERSION,
    MODEL_ID,
    ClearGhiCalibration,
    calibration_from_bytes,
    validate_calibration_for_run,
)

_STATE_KEYS = frozenset({
    "schema_version", "model_id", "basis_version", "site_fingerprint",
    "calibration_pack", "calibration_sha256", "training_as_of", "clear_days",
})
_RECORD_KEYS = frozenset({
    "date", "actual_sha256", "certification_sha256", "actual_available_at",
    "certification_available_at", "certification_rule_version", "certified_clear",
    "site_fingerprint", "basis_version", "hours", "actual_source", "certification_source",
})


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("clear-GHI store requires an ISO timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("clear-GHI store requires an aware timestamp")
    return stamp.astimezone(timezone.utc)


class ClearGhiStore:
    """Store completed packs; local publication requires one writer per site."""

    def __init__(
        self, directory: Path, *, fingerprint: str,
        storage: Any = None, cloud_prefix: str | None = None,
        deadline: float | None = None,
    ) -> None:
        if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise ValueError("clear-GHI store requires a SHA256 site fingerprint")
        if (storage is None) != (cloud_prefix is None):
            raise ValueError("clear-GHI cloud storage and prefix must be supplied together")
        self.fingerprint = fingerprint
        self.published_state_sha256: str | None = None
        self.deadline = time.monotonic() + 60.0 if deadline is None else deadline
        if not math.isfinite(self.deadline):
            raise ValueError("clear-GHI store requires a finite monotonic deadline")
        self.directory = Path(directory) / "clear_ghi" / fingerprint
        self.bucket: Any = None
        self.object_prefix: str | None = None
        if cloud_prefix is not None:
            match = re.fullmatch(r"gs://([^/]+)(?:/(.*))?", cloud_prefix.rstrip("/"))
            if match is None:
                raise ValueError("clear-GHI archive prefix must be a gs URI")
            self.bucket = storage.bucket(match.group(1))
            base = match.group(2) or ""
            self.object_prefix = "/".join(part for part in (base, "clear_ghi", fingerprint) if part)

    def _blob(self, name: str) -> Any:
        return self.bucket.blob(f"{self.object_prefix}/{name}")

    def remaining_timeout(self, *, cap: float = 4.0) -> float:
        """Share one operation deadline with acquisition; SDK retries stay disabled."""
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError("clear-GHI timeout cap must be positive and finite")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("clear-GHI background operation deadline exceeded")
        return min(cap, remaining)

    def check_deadline(self) -> None:
        """Fail at a CPU/local-I/O boundary when the shared deadline has elapsed."""
        self.remaining_timeout()

    def _read_original(self, name: str) -> tuple[bytes, int]:
        self.remaining_timeout()
        if self.bucket is not None:
            blob = self._blob(name)
            blob.reload(timeout=self.remaining_timeout(), retry=None)
            generation = int(blob.generation)
            raw = blob.download_as_bytes(if_generation_match=generation,
                                         timeout=self.remaining_timeout(), retry=None)
        else:
            raw = (self.directory / name).read_bytes()
            generation = int(_sha(raw), 16)
        self.remaining_timeout()
        return raw, generation

    def _validate_state(self, state: object) -> dict[str, Any]:
        if not isinstance(state, dict) or set(state) != _STATE_KEYS:
            raise ValueError("invalid clear-GHI current schema")
        if type(state["schema_version"]) is not int or state["schema_version"] != 1:
            raise ValueError("invalid clear-GHI current schema version")
        if (state["model_id"] != MODEL_ID or state["basis_version"] != BASIS_VERSION
                or state["site_fingerprint"] != self.fingerprint):
            raise ValueError("clear-GHI current model/basis/site mismatch")
        calibration = calibration_from_bytes(
            canonical_bytes(state["calibration_pack"]), sha256=state["calibration_sha256"],
        )
        if (calibration.site_fingerprint != self.fingerprint
                or _instant(state["training_as_of"]) != calibration.training_as_of.astimezone(timezone.utc)):
            raise ValueError("clear-GHI current calibration binding mismatch")
        records = state["clear_days"]
        if not isinstance(records, list):
            raise ValueError("clear-GHI current records must be an array")
        dates = []
        for record in records:
            if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
                raise ValueError("invalid clear-GHI original record schema")
            day = date.fromisoformat(record["date"])
            if day.isoformat() != record["date"]:
                raise ValueError("clear-GHI original requires an ISO date")
            dates.append(record["date"])
            if (record["site_fingerprint"] != self.fingerprint or record["basis_version"] != BASIS_VERSION
                    or record["certification_rule_version"] != state["calibration_pack"]["certification_rule_version"]
                    or record["certified_clear"] is not True):
                raise ValueError("clear-GHI original certification/site/basis mismatch")
            for source in ("actual", "certification"):
                original = record[f"{source}_source"]
                if not isinstance(original, dict) or original.get("date") != record["date"]:
                    raise ValueError("clear-GHI source original date mismatch")
                allowed_keys = {"date", "samples"} if source == "actual" else {"date", "model", "rule", "hourly"}
                if set(original) != allowed_keys or _sha(canonical_bytes(original)) != record[f"{source}_sha256"]:
                    raise ValueError("clear-GHI source original hash/schema mismatch")
                if _instant(record[f"{source}_available_at"]) > calibration.training_as_of.astimezone(timezone.utc):
                    raise ValueError("clear-GHI source original was unavailable at training cutoff")
            hours = record["hours"]
            if not isinstance(hours, list) or len(hours) != 13:
                raise ValueError("clear-GHI original requires thirteen hourly pairs")
            if (any(not isinstance(row, dict) or type(row.get("hour")) is not int for row in hours)
                    or [row.get("hour") for row in hours] != list(range(5, 18))):
                raise ValueError("clear-GHI original hours must be unique and ordered")
            for row in hours:
                pair = row.get("actual_halfhour_kwh")
                if (not isinstance(pair, list) or len(pair) != 2
                        or any(isinstance(value, bool) or not isinstance(value, (int, float))
                               or not math.isfinite(value) or value < 0 for value in pair)):
                    raise ValueError("clear-GHI original requires complete finite halfhour pairs")
            samples = [{"hour": row["hour"], "actual_halfhour_kwh": row["actual_halfhour_kwh"]} for row in hours]
            if record["actual_source"]["samples"] != samples:
                raise ValueError("clear-GHI actual hours differ from the hashed original")
            if record["certification_source"]["rule"] != record["certification_rule_version"]:
                raise ValueError("clear-GHI certification rule differs from the hashed original")
        if dates != sorted(set(dates)):
            raise ValueError("clear-GHI current dates must be unique and ordered")
        by_date = {record["date"]: record for record in records}
        for source in ("actual", "certification"):
            try:
                selected = [{"date": day.isoformat(), "sha256": by_date[day.isoformat()][f"{source}_sha256"]}
                            for day in calibration.training_dates]
            except KeyError as exc:
                raise ValueError("clear-GHI training original missing") from exc
            if _sha(canonical_bytes(selected)) != state["calibration_pack"][f"{source}_source_sha256"]:
                raise ValueError("clear-GHI training original aggregate hash mismatch")
        return state

    def read_current(self) -> tuple[dict[str, Any] | None, int]:
        """Read one current object; only genuine absence is a cold start."""
        try:
            raw, generation = self._read_original("current.json")
        except (NotFound, FileNotFoundError):
            return None, 0
        try:
            state = json.loads(raw)
            if canonical_bytes(state) != raw:
                raise ValueError("clear-GHI current is not canonical JSON")
            validated = self._validate_state(state)
            self.check_deadline()
            return validated, generation
        except (TypeError, KeyError, UnicodeError, OverflowError) as exc:
            raise ValueError("invalid clear-GHI current data") from exc

    def read_recovery_generation(self) -> int:
        """Inspect the raw current generation explicitly, including corrupt current."""
        try:
            _, generation = self._read_original("current.json")
        except (NotFound, FileNotFoundError):
            return 0
        return generation

    def _save_original(self, name: str, raw: bytes) -> None:
        self.remaining_timeout()
        if self.bucket is not None:
            blob = self._blob(name)
            try:
                blob.upload_from_string(raw, content_type="application/json",
                                        if_generation_match=0, timeout=self.remaining_timeout(), retry=None)
            except PreconditionFailed:
                # Existing hash-named originals are accepted only after byte verification.
                pass
            stored, _ = self._read_original(name)
        else:
            destination = self.directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".original-", suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    # Publish completed bytes exclusively; a crash cannot leave a partial original.
                    os.link(temporary, destination)
                except FileExistsError:
                    pass
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            stored = destination.read_bytes()
        if stored != raw or _sha(stored) != _sha(raw):
            raise ValueError("clear-GHI immutable original read-back mismatch")
        self.remaining_timeout()

    def publish(
        self, calibration: ClearGhiCalibration, records: list[dict[str, Any]], *, expected_generation: int,
    ) -> None:
        """Save verified originals first, then replace current once without retries."""
        self.remaining_timeout()
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("clear-GHI expected generation must be a nonnegative integer")
        calibration_from_bytes(calibration.raw_bytes, sha256=calibration.sha256)
        candidate = {
            "schema_version": 1, "model_id": MODEL_ID, "basis_version": BASIS_VERSION,
            "site_fingerprint": self.fingerprint,
            "calibration_pack": json.loads(calibration.raw_bytes), "calibration_sha256": calibration.sha256,
            "training_as_of": calibration.training_as_of.isoformat(), "clear_days": records,
        }
        raw = canonical_bytes(candidate)
        try:
            candidate = self._validate_state(json.loads(raw))
        except (TypeError, KeyError, UnicodeError, OverflowError) as exc:
            raise ValueError("invalid clear-GHI publication data") from exc
        previous, generation = self.read_current()
        if previous is not None:
            old_time, new_time = _instant(previous["training_as_of"]), _instant(candidate["training_as_of"])
            if new_time < old_time:
                raise ValueError("clear-GHI late updater cannot replace a newer current")
            if new_time == old_time and previous["calibration_sha256"] != calibration.sha256:
                raise ValueError("clear-GHI different calibrations conflict at the same cutoff")
            if canonical_bytes(previous) == raw:
                self.published_state_sha256 = _sha(raw)
                return
        if generation != expected_generation:
            raise ValueError("clear-GHI current generation conflict")
        previous_records = {record["date"]: record for record in previous["clear_days"]} if previous else {}
        for record in candidate["clear_days"]:
            for source in ("actual", "certification"):
                prior = previous_records.get(record["date"])
                if prior is not None and prior[f"{source}_sha256"] == record[f"{source}_sha256"]:
                    continue
                name = f"originals/{source}/{record['date']}/{record[f'{source}_sha256']}.json"
                self._save_original(name, canonical_bytes(record[f"{source}_source"]))
        self._save_original(f"calibrations/{calibration.sha256}.json", calibration.raw_bytes)
        self._save_original(f"states/{_sha(raw)}.json", raw)
        self._replace_current(raw, expected_generation=expected_generation)

    def _replace_current(self, raw: bytes, *, expected_generation: int) -> None:
        self.remaining_timeout()
        if self.bucket is not None:
            self._blob("current.json").upload_from_string(
                raw, content_type="application/json", if_generation_match=expected_generation,
                timeout=self.remaining_timeout(), retry=None,
            )
            self.published_state_sha256 = _sha(raw)
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".current-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            self.remaining_timeout()
            os.replace(temporary, self.directory / "current.json")
            self.published_state_sha256 = _sha(raw)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def restore_current(
        self, state_sha256: str, *, expected_generation: int, target_date: date, as_of: datetime,
    ) -> None:
        """Explicitly restore missing/corrupt current from a selected immutable state hash."""
        if re.fullmatch(r"[0-9a-f]{64}", state_sha256) is None:
            raise ValueError("clear-GHI recovery requires a selected state SHA256")
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("clear-GHI recovery requires a nonnegative generation")
        raw, _ = self._read_original(f"states/{state_sha256}.json")
        if _sha(raw) != state_sha256:
            raise ValueError("clear-GHI recovery state hash mismatch")
        try:
            state = json.loads(raw)
            if canonical_bytes(state) != raw:
                raise ValueError("clear-GHI recovery state is not canonical")
            state = self._validate_state(state)
        except (TypeError, KeyError, UnicodeError, OverflowError) as exc:
            raise ValueError("invalid clear-GHI recovery state") from exc
        calibration_raw, _ = self._read_original(f"calibrations/{state['calibration_sha256']}.json")
        calibration = calibration_from_bytes(calibration_raw, sha256=state["calibration_sha256"])
        if calibration_raw != canonical_bytes(state["calibration_pack"]):
            raise ValueError("clear-GHI recovery pack binding mismatch")
        validate_calibration_for_run(calibration, expected_site=self.fingerprint,
                                     expected_basis=BASIS_VERSION, target_date=target_date, as_of=as_of)
        for record in state["clear_days"]:
            for source in ("actual", "certification"):
                name = f"originals/{source}/{record['date']}/{record[f'{source}_sha256']}.json"
                original, _ = self._read_original(name)
                if original != canonical_bytes(record[f"{source}_source"]):
                    raise ValueError("clear-GHI recovery source binding mismatch")
        try:
            current_raw, generation = self._read_original("current.json")
        except (NotFound, FileNotFoundError):
            generation = 0
        else:
            try:
                current_state = json.loads(current_raw)
                if canonical_bytes(current_state) != current_raw:
                    raise ValueError("clear-GHI current is not canonical")
                self._validate_state(current_state)
            except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
                pass
            else:
                if current_raw == raw:
                    self.published_state_sha256 = state_sha256
                    return
                raise ValueError("clear-GHI recovery cannot replace a valid current")
        if generation != expected_generation:
            raise ValueError("clear-GHI recovery generation conflict")
        self._replace_current(raw, expected_generation=expected_generation)
