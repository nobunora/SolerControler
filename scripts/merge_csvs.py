from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.csv_merge import discover_csv_files, merge_csv_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge CSV files from csv folders into one CSV.")
    parser.add_argument("--input-root", default="artifacts", help="Search root for csv folders. Default: artifacts")
    parser.add_argument("--output", default="", help="Output CSV path. Default: artifacts/combined_csv/merged-<timestamp>.csv")
    parser.add_argument("--csv-dir-name", default="csv", help="Directory name that contains CSV files. Default: csv")
    parser.add_argument("--include-source-file", action="store_true", help="Add source_file column with relative source path.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_root = Path(args.input_root).expanduser().resolve()
    if not input_root.exists():
        raise FileNotFoundError(f"Input root not found: {input_root}")

    csv_files = discover_csv_files(input_root, csv_dir_name=args.csv_dir_name)
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found under '{input_root}' in folders named '{args.csv_dir_name}'.")

    if args.output:
        output = Path(args.output).expanduser().resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = (input_root / "combined_csv" / f"merged-{stamp}.csv").resolve()

    result = merge_csv_files(
        csv_files,
        output,
        include_source_file=args.include_source_file,
        source_root=input_root,
    )

    payload = {
        "input_root": str(input_root),
        "csv_dir_name": args.csv_dir_name,
        "output": str(result.output_path),
        "source_file_count": len(result.source_files),
        "row_count": result.row_count,
        "duplicate_count": result.duplicate_count,
        "includes_source_file": result.includes_source_file,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
