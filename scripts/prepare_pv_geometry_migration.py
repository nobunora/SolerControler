"""Prepare a separate native ledger from immutable source observations, never publish."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.forecasting.pv_array import load_pv_array_configs
from app.forecasting.pv_ideal_max import HourlyPvObservation, IdealMaxArray, initial_ideal_max_state, update_ideal_max
from app.forecasting.pv_ideal_max_cloud import IdealMaxCloudArchive
from app.forecasting.pv_ideal_max_store import IdealMaxStore
from app.operations.pv_ideal_max import configured_ideal_max_geometry, ideal_max_archive_prefix


def prepare(*, source_arrays: Path, output: Path, source_ledger: Path | None = None) -> dict[str, Any]:
    if output.exists():
        raise ValueError("migration output already exists; do not overwrite or retry a publish")
    target = configured_ideal_max_geometry()
    source = replace(target, arrays=tuple(IdealMaxArray(a.azimuth_deg, a.tilt_deg, a.capacity_kw,
                     a.performance_ratio, a.shading_factor) for a in load_pv_array_configs(source_arrays)))
    if source.fingerprint == target.fingerprint:
        raise ValueError("geometry migration requires distinct fingerprints")
    output.mkdir(parents=True)
    source_receipt: dict[str, Any] = {}
    if source_ledger is None:
        from google.cloud.storage import Client
        archive = IdealMaxCloudArchive(Client(), ideal_max_archive_prefix(), source)
        store = archive.checkout(output / "source")
        if store is None:
            raise ValueError("source cloud ledger is missing")
        source_ledger = store.path
        store.close()
        source_receipt = {"source_snapshot_sha256": archive.original["sha256"] if archive.original else None,
                          "source_generation": archive.generation}
    source_hash = hashlib.sha256(source_ledger.read_bytes()).hexdigest()
    with sqlite3.connect(source_ledger.resolve().as_uri() + "?mode=ro", uri=True) as db:
        saved_raw, saved_hash = db.execute("SELECT payload,sha256 FROM current_state WHERE singleton=1").fetchone()
        if hashlib.sha256(saved_raw).hexdigest() != saved_hash:
            raise ValueError("source state checksum mismatch")
        saved = json.loads(saved_raw)
        if saved["fingerprint"] != source.fingerprint:
            raise ValueError("source geometry mismatch")
        records = [json.loads(r[0]) for r in db.execute("SELECT payload FROM observations LIMIT ?", (366 * 24 + 1,))]
    if not records or len(records) > 366 * 24:
        raise ValueError("missing observations or migration read budget exceeded")
    observations = [HourlyPvObservation(datetime.fromisoformat(r["interval_start"]), r["halfhour_kwh"][0], r["halfhour_kwh"][1],
                    datetime.fromisoformat(r["available_at"])) for r in records]
    if any(o.source_sha256 != r["source_sha256"] for o, r in zip(observations, records)):
        raise ValueError("source observation checksum mismatch")
    observations.sort(key=lambda o: (o.available_at, o.interval_start))
    initial = observations[0].available_at - timedelta(microseconds=1)
    old_state = initial_ideal_max_state(source, as_of=initial)
    for observation in observations:
        old_state = update_ideal_max(old_state, source, observation, as_of=observation.available_at).state_after
    if old_state.coefficient != saved["coefficient"] or old_state.revision != saved["revision"]:
        raise ValueError("source state cannot be reproduced from the complete history")
    seed = output / "seed"
    with IdealMaxStore(seed, target, initialize_at=initial) as new:
        for observation in observations:
            new.ingest(observation, as_of=observation.available_at)
        state = new.read_current()
        # No historical predictions are issued by this migration.
        if new.connection.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] != 0:
            raise ValueError("migration must not issue past predictions")
    if hashlib.sha256(source_ledger.read_bytes()).hexdigest() != source_hash:
        raise ValueError("source ledger changed")
    result = {"schema_version": 1, "status": "PREPARED_NOT_PUBLISHED", "created_at": datetime.now(timezone.utc).isoformat(),
              "source_geometry": asdict(source), "target_fingerprint": target.fingerprint,
              "source_file_sha256": source_hash, "observations": len(observations), "source_reproduced": True,
              "source_coefficient": old_state.coefficient, "target_coefficient": state.coefficient,
              "target_revision": state.revision, "past_predictions_issued": 0,
              "target_ledger_sha256": hashlib.sha256((seed / "pv_ideal_max" / target.fingerprint / "ledger.sqlite3").read_bytes()).hexdigest(),
              **source_receipt}
    (output / "manifest.private.json").write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-array-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-ledger", type=Path)
    args = parser.parse_args(argv)
    result = prepare(source_arrays=args.source_array_config, output=args.output, source_ledger=args.source_ledger)
    print(json.dumps({k: result[k] for k in ("status", "observations", "source_reproduced", "source_coefficient", "target_coefficient", "past_predictions_issued")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
