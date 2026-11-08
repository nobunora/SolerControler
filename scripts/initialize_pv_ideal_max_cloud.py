"""Validate a learned local ledger; publish only with an explicit --publish."""

from __future__ import annotations

import argparse
import json
import hashlib
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.forecasting.pv_ideal_max_cloud import IdealMaxCloudArchive
from app.forecasting.pv_ideal_max_store import IdealMaxStore
from app.forecasting.pv_ideal_max import IdealMaxArray
from app.operations.pv_ideal_max import configured_ideal_max_geometry, ideal_max_archive_prefix


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--migration-manifest", type=Path)
    args = parser.parse_args(argv)
    geometry = configured_ideal_max_geometry()
    prefix = ideal_max_archive_prefix()
    if not prefix:
        parser.error("existing archive prefix is not configured")
    with IdealMaxStore(args.state_dir, geometry) as store:
        state = store.read_current()
        result = {"status": "VALIDATED_LOCAL_SEED", "coefficient": state.coefficient, "revision": state.revision}
        if args.publish:
            from google.cloud.storage import Client
            if args.migration_manifest:
                manifest = json.loads(args.migration_manifest.read_text(encoding="utf-8"))
                source_value = manifest["source_geometry"]
                source = replace(geometry, arrays=tuple(IdealMaxArray(**a) for a in source_value["arrays"]))
                if (manifest.get("target_fingerprint") != geometry.fingerprint or not manifest.get("source_reproduced")
                        or manifest.get("target_coefficient") != state.coefficient
                        or source_value["latitude"] != geometry.latitude or source_value["longitude"] != geometry.longitude
                        or source_value["timezone_name"] != geometry.timezone_name
                        or manifest.get("target_ledger_sha256") != hashlib.sha256(store.path.read_bytes()).hexdigest()):
                    parser.error("migration manifest does not match this seed or site")
                prior = IdealMaxCloudArchive(Client(), prefix, source)
                with tempfile.TemporaryDirectory(prefix="ideal-max-source-check-") as check:
                    old = prior.checkout(Path(check))
                    if old is None:
                        parser.error("migration source is unavailable")
                    old.close()
                    if (prior.original is None or prior.original["sha256"] != manifest.get("source_snapshot_sha256")
                            or prior.generation != manifest.get("source_generation")):
                        parser.error("migration source changed; prepare a new reviewed seed")
            archive = IdealMaxCloudArchive(Client(), prefix, geometry)
            with tempfile.TemporaryDirectory(prefix="ideal-max-init-check-") as temporary:
                current = archive.checkout(Path(temporary))
                if current is not None:
                    current.close()
                    parser.error("cloud ledger already exists; initialization cannot overwrite it")
            result = {"status": "INITIALIZED", **archive.publish(store, initialize=True)}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
