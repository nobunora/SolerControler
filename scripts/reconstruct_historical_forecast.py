from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.operations.firestore import open_firestore
from app.operations.historical_forecast_reconstruction import (
    ALLOWED_RECONSTRUCTION_BASES,
    persist_reconstructed_forecast_plan,
    preview_reconstructed_forecast_plan,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or persist an explicitly labeled historical forecast reconstruction. "
            "Default mode is read-only preview; --apply writes only reconstruction collections."
        )
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument(
        "--basis",
        required=True,
        choices=sorted(ALLOWED_RECONSTRUCTION_BASES),
    )
    parser.add_argument(
        "--input-provenance",
        required=True,
        help="Sanitized historical-input evidence descriptor; do not include secrets.",
    )
    parser.add_argument("--reconstructed-at", default=None)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist to forecast_hourly_reconstructed / forecast_reconstructions.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    summary = preview_reconstructed_forecast_plan(
        plan_path=args.plan,
        target_date=args.target_date,
        reconstruction_model_version=args.model_version,
        reconstruction_basis=args.basis,
        input_provenance=args.input_provenance,
        reconstructed_at=args.reconstructed_at,
    )
    print(json.dumps({"mode": "preview", **summary}, ensure_ascii=False, sort_keys=True))
    if not args.apply:
        return 0

    inserted = persist_reconstructed_forecast_plan(
        open_firestore(),
        plan_path=args.plan,
        target_date=args.target_date,
        reconstruction_model_version=args.model_version,
        reconstruction_basis=args.basis,
        input_provenance=args.input_provenance,
        reconstructed_at=summary["forecast_reconstructed_at"],
    )
    print(
        json.dumps(
            {
                "mode": "apply",
                "inserted_hourly_rows": inserted,
                "forecast_reconstruction_id": summary["forecast_reconstruction_id"],
                "date": summary["date"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
