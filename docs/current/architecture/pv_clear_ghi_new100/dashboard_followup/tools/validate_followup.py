"""Validate the follow-up document routing and explicit source boundaries only."""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[4]
HEADINGS = ["入口・参照範囲", "変更対象", "変更禁止", "クラス・変数差分", "実装手順・分岐", "テスト・PASS条件", "中断条件", "出口・引継ぎ"]


def python_symbols(path: Path) -> set[str]:
    found: set[str] = set()
    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                found.add(name)
                visit(node.body, name + ".")
    visit(ast.parse(path.read_text(encoding="utf-8-sig")).body)
    return found


def check(index: dict, boundaries: dict, inventory: dict, *, sources: bool = True) -> list[str]:
    errors = []
    steps = index["steps"]
    ids = [step["id"] for step in steps]
    if len(ids) != len(set(ids)) or set(ids) != {f"D{i:02d}" for i in range(1, 7)}:
        errors.append("invalid step set")
    risk_ids = {risk["id"] for risk in inventory["risks"]}
    display_ids = {item["id"] for item in inventory["inventory"]}
    if len(display_ids) != 81 or len(risk_ids) != 18:
        errors.append("incomplete inventory")
    resolved = {item["risk"] for item in index["risk_resolution"]}
    if resolved != risk_ids:
        errors.append("unresolved risk")
    for resolution in index["risk_resolution"]:
        owners = resolution["implementation_steps"] + resolution["retention_steps"]
        if not owners or not set(owners) <= set(ids):
            errors.append("invalid risk owner")
        if resolution["action"] not in {"PLANNED_DISPLAY_FIX", "NOT_FIXED_RETAIN_OR_EXPLICIT_RESIDUAL"}:
            errors.append("unknown risk action")
    by_id = {step["id"]: step for step in steps}
    visiting: set[str] = set()
    visited: set[str] = set()
    def walk(step_id: str) -> None:
        if step_id in visiting:
            errors.append("dependency cycle")
            return
        if step_id in visited:
            return
        visiting.add(step_id)
        for dependency in by_id[step_id]["depends_on"]:
            if dependency not in by_id:
                errors.append("unknown dependency")
            else:
                walk(dependency)
        visiting.remove(step_id)
        visited.add(step_id)
    covered: set[str] = set()
    for step in steps:
        walk(step["id"])
        content = (PACKAGE / step["file"]).read_text(encoding="utf-8")
        if re.findall(r"^## (.+)$", content, re.M) != HEADINGS:
            errors.append("step headings missing")
        if not set(step["risk_ids"]) <= risk_ids or not set(step["display_ids"]) <= display_ids:
            errors.append("unknown risk/display reference")
        covered.update(step["display_ids"])
        verified = step["tests_status"] == "VERIFIED_LOCAL" and index.get("local_verification", {}).get("result") == "PASS"
        if step["tests_status"] != "PLANNED_NOT_EXECUTED" and not verified:
            errors.append("unproven test claim")
        for doc in step["read_set"]:
            if not (PACKAGE / doc).is_file():
                errors.append("missing readset")
    if covered != display_ids:
        errors.append("display coverage gap")
    if index.get("local_verification") and not (PACKAGE / index["local_verification"].get("report", "missing.md")).is_file():
        errors.append("missing verification report")
    if set(index["backend_gap_resolution"]) != {f"DF{i:02d}" for i in range(1, 8)}:
        errors.append("backend gap coverage incomplete")
    for entry in boundaries["allowed"]:
        if entry["step"] not in ids or not entry["variables"] or not (PACKAGE / entry["exact_scope"]).is_file():
            errors.append("invalid proposed boundary")
        authorized = index.get("implementation_authorization", {}).get("scope") == "D01-D05+C08+C09"
        expected_authority = "USER_AUTHORIZED_2026_10_12_BOUNDED_IMPLEMENTATION" if authorized else "PROPOSED_ONLY_REQUIRES_NEXT_IMPLEMENTATION_INSTRUCTION"
        if entry["authority"] != expected_authority:
            errors.append("unapproved edit authority")
        path = ROOT / entry["path"]
        if not path.is_file():
            errors.append("missing source path")
        if sources and path.is_file() and entry["kind"] == "existing":
            names = python_symbols(path) if path.suffix == ".py" else set(re.findall(r"\bfunction\s+(\w+)\s*\(", path.read_text(encoding="utf-8")))
            if entry["symbol"] not in names:
                errors.append("missing existing symbol: " + entry["symbol"])
    if sources:
        for path, expected in index.get("verified_source_sha256", {}).items():
            if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
                errors.append("verified source changed: " + path)
        allowed_paths = {entry["path"] for entry in boundaries["allowed"]} if index.get("implementation_authorization") else set()
        for path, expected in index["source_sha256"].items():
            if path in allowed_paths:
                continue
            if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
                errors.append("reviewed source changed: " + path)
        for protected in boundaries["protected"]:
            path = ROOT / protected["path"]
            names = python_symbols(path) if path.suffix == ".py" else set(re.findall(r"\bfunction\s+(\w+)\s*\(", path.read_text(encoding="utf-8")))
            if not set(protected["symbols"]) <= names:
                errors.append("protected symbol missing: " + protected["path"])
    for doc in index["documents"] + [step["file"] for step in steps]:
        path = PACKAGE / doc
        if path.suffix != ".md":
            continue
        for link in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = link.split("#", 1)[0]
            if target and not target.startswith(("http://", "https://")) and not (path.parent / target).exists():
                errors.append("broken link: " + doc + " -> " + link)
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--step", choices=[f"D{i:02d}" for i in range(1, 7)])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    index = json.loads((PACKAGE / "index.json").read_text(encoding="utf-8"))
    boundaries = json.loads((PACKAGE / "boundaries.json").read_text(encoding="utf-8"))
    inventory = json.loads((PACKAGE / "ui-display-inventory.json").read_text(encoding="utf-8"))
    if args.step:
        step = next(entry for entry in index["steps"] if entry["id"] == args.step)
        print(json.dumps(step, ensure_ascii=True, indent=2))
        return 0
    errors = check(index, boundaries, inventory)
    rejected = []
    if args.self_test:
        for kind in ["cycle", "unresolved-risk", "bad-owner", "unknown-display", "missing-readset", "unproven-pass", "invalid-boundary", "edit-authority"]:
            fixture = copy.deepcopy(index)
            limits = copy.deepcopy(boundaries)
            if kind == "cycle":
                fixture["steps"][0]["depends_on"] = ["D05"]
            elif kind == "unresolved-risk":
                fixture["risk_resolution"].pop()
            elif kind == "bad-owner":
                fixture["risk_resolution"][0]["implementation_steps"] = ["UNKNOWN"]
            elif kind == "unknown-display":
                fixture["steps"][0]["display_ids"].append("U999")
            elif kind == "missing-readset":
                fixture["steps"][0]["read_set"].append("absent.md")
            elif kind == "unproven-pass":
                fixture["steps"][0]["tests_status"] = "PASS"
            elif kind == "invalid-boundary":
                limits["allowed"][0]["variables"] = []
            elif kind == "edit-authority":
                limits["allowed"][0]["authority"] = "UNLIMITED"
            if check(fixture, limits, inventory, sources=False):
                rejected.append(kind)
            else:
                errors.append("negative fixture accepted: " + kind)
    result = {"result": "PASS" if not errors else "FAIL", "scope": "DOCUMENTATION_AND_SOURCE_STATE_ONLY",
              "steps": len(index["steps"]), "display_items": len(inventory["inventory"]), "risk_items": len(inventory["risks"]),
              "backend_gaps": len(index["backend_gap_resolution"]), "source_hashes": len(index["source_sha256"]),
              "errors": errors, "negative_fixtures_rejected": rejected}
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
