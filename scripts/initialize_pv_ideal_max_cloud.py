"""Validate a learned local ledger; publish only with an explicit --publish."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.forecasting.pv_ideal_max_cloud import IdealMaxCloudArchive
from app.forecasting.pv_ideal_max_store import IdealMaxStore
from app.operations.pv_ideal_max import configured_ideal_max_geometry, ideal_max_archive_prefix


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--publish", action="store_true")
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
