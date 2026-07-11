from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.firestore_ops import open_firestore
from app.night_plan_archive import (
    build_night_plan_firestore_document,
    load_night_plan_detail_from_firestore_doc,
    night_plan_gcs_uri,
    upload_night_plan_to_gcs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive full night_charge_plans detail JSON to GCS.")
    parser.add_argument("--apply", action="store_true", help="Write Firestore/GCS changes. Default is dry-run.")
    parser.add_argument("--include-latest", action="store_true", help="Also rewrite night_charge_plans/latest.")
    parser.add_argument("--rebuild-summary", action="store_true", help="Rebuild Firestore summary fields from archived detail.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    client = open_firestore()
    docs = list(client.collection("night_charge_plans").stream())
    processed = 0
    changed = 0
    for snap in docs:
        if snap.id == "latest" and not args.include_latest:
            continue
        data = snap.to_dict() or {}
        existing_uri = str(data.get("detail_gcs_uri") or data.get("detail_uri") or "").strip()
        if args.apply and not args.rebuild_summary and snap.id != "latest" and existing_uri and data.get("detail_sha256"):
            snap.reference.set(
                {
                    "plan_json": None,
                    "detail_storage": "gcs",
                    "detail_uri": existing_uri,
                    "detail_gcs_uri": existing_uri,
                    "detail_retention_policy": "indefinite",
                    "detail_retention_delete_after": None,
                    "detail_inline_until_days": 0,
                },
                merge=True,
            )
            processed += 1
            changed += 1
            print(f"updated-inline\t{snap.id}\t{existing_uri}\tinline=False", flush=True)
            if args.limit and processed >= args.limit:
                break
            continue
        try:
            plan = load_night_plan_detail_from_firestore_doc(data)
        except Exception as exc:
            print(f"skip\t{snap.id}\tload_failed\t{exc}", flush=True)
            continue
        if not isinstance(plan, dict):
            print(f"skip\t{snap.id}\tmissing_detail", flush=True)
            continue
        forecast = plan.get("forecast") if isinstance(plan.get("forecast"), dict) else {}
        plan_date = str(forecast.get("date") or data.get("date") or "").strip()
        if not plan_date:
            print(f"skip\t{snap.id}\tmissing_date", flush=True)
            continue
        uri = night_plan_gcs_uri(forecast_date=plan_date)
        if not uri:
            print(f"skip\t{snap.id}\tgcs_prefix_not_configured", flush=True)
            continue
        source = str(data.get("source") or "archive-backfill")
        updated_at = str(data.get("updated_at") or datetime.now(timezone.utc).isoformat())
        processed += 1
        if args.apply:
            archive = upload_night_plan_to_gcs(plan, forecast_date=plan_date)
            doc = build_night_plan_firestore_document(
                plan,
                source=source,
                updated_at=updated_at,
                force_inline_detail=(snap.id == "latest"),
                archive_info=archive,
            )
            snap.reference.set(doc, merge=True)
            changed += 1
            print(f"archived\t{snap.id}\t{uri}\tinline={doc.get('plan_json') is not None}", flush=True)
        else:
            preview = build_night_plan_firestore_document(
                plan,
                source=source,
                updated_at=updated_at,
                archive_info={"detail_uri": uri},
            )
            print(f"dry-run\t{snap.id}\t{uri}\tinline={preview.get('plan_json') is not None}", flush=True)
        if args.limit and processed >= args.limit:
            break
    print(f"[archive] processed={processed} changed={changed} apply={args.apply}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
