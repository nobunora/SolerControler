"""Validate documentation contracts without changing production files."""

from __future__ import annotations

import argparse
import ast
import copy
import json
import re
from pathlib import Path
from typing import Any

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[3]
HEADINGS = (
    "入口・参照範囲", "変更対象", "変更禁止", "クラス・変数差分",
    "実装手順・分岐", "テスト・PASS条件", "中断条件", "出口・引継ぎ",
)


def load_json(name: str) -> dict[str, Any]:
    return json.loads((PACKAGE / name).read_text(encoding="utf-8"))


def source_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found: set[str] = set()

    def visit(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                found.add(name)
                visit(node.body, name + ".")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for child in ast.walk(target):
                        if isinstance(child, ast.Name):
                            found.add(prefix + child.id)

    visit(tree.body)
    return found


def check_package(
    index: dict[str, Any], boundaries: dict[str, Any], gates: dict[str, Any],
    documents: dict[str, str], *, check_sources: bool = True,
) -> list[str]:
    errors: list[str] = []
    steps = index["steps"]
    ids = [s["id"] for s in steps]
    gate_ids = [g["id"] for g in gates["gates"]]
    test_ids = [t for s in steps for t in s["test_ids"]]
    check_ids = [c["id"] for c in index["checks"]]
    for label, values in [("step", ids), ("gate", gate_ids), ("test", test_ids), ("check", check_ids)]:
        if len(values) != len(set(values)):
            errors.append(f"duplicate {label} ID")
    if set(ids) != {f"S{i:02}" for i in range(11)}:
        errors.append("step set must be S00..S10")
    expected_checks = {f"R{i:02}" for i in range(1, 16)} | {f"C{i:02}" for i in range(1, 17)}
    if set(check_ids) != expected_checks:
        errors.append("check set must be R01..R15/C01..C16")
    by_id = {s["id"]: s for s in steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(step_id: str) -> None:
        if step_id in visiting:
            errors.append(f"dependency cycle at {step_id}")
            return
        if step_id in visited or step_id not in by_id:
            return
        visiting.add(step_id)
        for dependency in by_id[step_id]["depends_on"]:
            walk(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step in steps:
        sid = step["id"]
        walk(sid)
        if any(d not in ids for d in step["depends_on"]):
            errors.append(f"{sid}: unknown dependency")
        if any(g not in gate_ids for g in step["gates"]):
            errors.append(f"{sid}: unknown gate")
        if any(c not in check_ids for c in step["coverage_ids"]):
            errors.append(f"{sid}: unknown coverage")
        content = documents.get(step["file"], "")
        if re.findall(r"^## (.+)$", content, re.M) != list(HEADINGS):
            errors.append(f"{sid}: missing/reordered headings")
        for heading in HEADINGS:
            segment = content.split("## " + heading + "\n", 1)
            if len(segment) < 2 or not segment[1].split("\n## ", 1)[0].strip():
                errors.append(f"{sid}: empty section {heading}")
        table_section = content.split("## テスト・PASS条件", 1)[-1].split("## 中断条件", 1)[0]
        for tid in step["test_ids"]:
            if not re.search(r"\b" + re.escape(tid) + r"\b", table_section):
                errors.append(f"{sid}: missing test {tid}")
        if "PASS" not in table_section or not re.search(r"PLANNED|EXISTING", table_section):
            errors.append(f"{sid}: test status/PASS absent")
        for path in step["read_set"]:
            if path not in documents and not (PACKAGE / path).is_file():
                errors.append(f"{sid}: missing read_set {path}")
        for path in re.findall(r"tests/[A-Za-z0-9_]+\.py", content):
            if not (REPO / path).is_file() and path not in index["planned_test_paths"]:
                errors.append(f"{sid}: unregistered planned test {path}")
    for item in index["checks"]:
        if not item["owner_steps"] or any(s not in ids for s in item["owner_steps"]):
            errors.append(f"{item['id']}: invalid owners")
        actual = {s["id"] for s in steps if item["id"] in s["coverage_ids"]}
        if actual != set(item["owner_steps"]):
            errors.append(f"{item['id']}: owner/coverage mismatch")
    for gate in gates["gates"]:
        for key in ["next_action", "pass_condition", "evidence_required", "stop_if", "approval"]:
            if not gate.get(key):
                errors.append(f"{gate['id']}: missing {key}")
        if gate["owner_step"] not in ids or gate["state"] not in {"OPEN", "CLOSED"}:
            errors.append(f"{gate['id']}: bad owner/state")
        if gate["state"] == "CLOSED" and not gate["evidence"]:
            errors.append(f"{gate['id']}: CLOSED without evidence")
    for filename, content in documents.items():
        for link in re.findall(r"\]\(([^)]+)\)", content):
            if link.startswith(("http:", "https:", "app:")):
                continue
            target = link.split("#", 1)[0]
            if target and not (PACKAGE / filename).parent.joinpath(target).is_file():
                errors.append(f"{filename}: broken link {link}")
    if check_sources:
        cache: dict[str, tuple[str, set[str]]] = {}
        for entry in boundaries["allowed"] + boundaries["protected"]:
            if entry["kind"] == "new":
                continue
            path = entry["path"]
            if not (REPO / path).is_file():
                errors.append(f"missing existing source {path}")
                continue
            if path not in cache:
                cache[path] = ((REPO / path).read_text(encoding="utf-8-sig"), source_symbols(REPO / path))
            content, symbols = cache[path]
            prefix = path.removesuffix(".py").replace("/", ".") + "."
            symbol = entry["symbol"].removeprefix(prefix)
            if symbol not in symbols:
                errors.append(f"undefined existing symbol {path}:{symbol}")
            for anchor in entry.get("anchors", []):
                if anchor not in content:
                    errors.append(f"missing protected anchor {path}:{anchor}")
        for entry in boundaries["allowed"]:
            if entry["step"] not in ids:
                errors.append("allowed boundary has unknown step")
        if any(not e.get("anchors") for e in boundaries["protected"]):
            errors.append("protected boundary lacks literal anchors")
        for entry in boundaries["protected"]:
            if not entry.get("applies_to") or any(s not in ids for s in entry["applies_to"]):
                errors.append(f"{entry['id']}: protected route is missing/unknown")
    return errors


def self_test(index: dict[str, Any], boundaries: dict[str, Any], gates: dict[str, Any], documents: dict[str, str]) -> list[str]:
    """Real negative cases ensure common contract failures are detected."""
    cases: list[tuple[str, str, Any]] = [
        ("duplicate-step", "index", lambda d: d["steps"].append(copy.deepcopy(d["steps"][0]))),
        ("unknown-gate", "index", lambda d: d["steps"][0]["gates"].append("G99")),
        ("cycle", "index", lambda d: d["steps"][0]["depends_on"].append("S10")),
        ("missing-test", "index", lambda d: d["steps"][0]["test_ids"].append("INPUT-999")),
        ("closed-without-evidence", "gates", lambda d: d["gates"][0].update(state="CLOSED", evidence=[])),
        ("unknown-owner", "index", lambda d: d["checks"][0].update(owner_steps=["S99"])),
        ("bad-anchor", "boundaries", lambda d: d["protected"][0]["anchors"].append("NONEXISTENT_PROTECTED_ANCHOR_999")),
        ("bad-symbol", "boundaries", lambda d: d["protected"][0].update(symbol="NONEXISTENT_SYMBOL_999")),
        ("missing-protected-route", "boundaries", lambda d: d["protected"][0].update(applies_to=[])),
    ]
    passed: list[str] = []
    for name, target, mutation in cases:
        values = copy.deepcopy({"index": index, "boundaries": boundaries, "gates": gates, "documents": documents})
        mutation(values[target])
        if not check_package(**values):
            raise AssertionError(f"negative fixture was accepted: {name}")
        passed.append(name)
    step_file = index["steps"][0]["file"]
    for name, replacement in [("missing-heading", "## REMOVED"), ("empty-pass", "")]:
        altered = dict(documents)
        if name == "missing-heading":
            altered[step_file] = altered[step_file].replace("## 中断条件", replacement)
        else:
            altered[step_file] = re.sub(r"(?<=## テスト・PASS条件\n).*?(?=\n## 中断条件)", "\n", altered[step_file], flags=re.S)
        if not check_package(index, boundaries, gates, altered):
            raise AssertionError(f"negative fixture was accepted: {name}")
        passed.append(name)
    altered = dict(documents)
    altered[step_file] += "\n[broken](missing-document-999.md)\n"
    if not check_package(index, boundaries, gates, altered):
        raise AssertionError("broken-link accepted")
    return passed + ["broken-link"]


def route(index: dict[str, Any], gates: dict[str, Any], boundaries: dict[str, Any], step_id: str) -> dict[str, Any]:
    by_id = {s["id"]: s for s in index["steps"]}
    if step_id not in by_id:
        raise ValueError(f"unknown step {step_id}")
    inherited: set[str] = set()

    def collect(sid: str) -> None:
        inherited.update(by_id[sid]["gates"])
        for dependency in by_id[sid]["depends_on"]:
            collect(dependency)

    collect(step_id)
    step = by_id[step_id]
    return {
        "step": step,
        "dependency_sections": [{"file": by_id[d]["file"], "section": "出口・引継ぎ"} for d in step["depends_on"]],
        "inherited_gates": [g for g in gates["gates"] if g["id"] in inherited],
        "allowed": [e for e in boundaries["allowed"] if e["step"] == step_id],
        "protected": [e for e in boundaries["protected"] if step_id in e["applies_to"]],
        "read_bytes": sum((PACKAGE / p).stat().st_size for p in step["read_set"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    index, boundaries, gates = load_json("index.json"), load_json("boundaries.json"), load_json("gates.json")
    documents = {p.relative_to(PACKAGE).as_posix(): p.read_text(encoding="utf-8") for p in PACKAGE.rglob("*.md")}
    errors = check_package(index, boundaries, gates, documents)
    result: dict[str, Any] = {"result": "FAIL" if errors else "PASS", "scope": "DOCUMENTATION_ONLY", "errors": errors}
    if not errors:
        result.update(steps=len(index["steps"]), checks=len(index["checks"]), protected=len(boundaries["protected"]), open_gates=[g["id"] for g in gates["gates"] if g["state"] == "OPEN"])
        if args.self_test:
            result["negative_fixtures_rejected"] = self_test(index, boundaries, gates, documents)
        if args.step:
            result["route"] = route(index, gates, boundaries, args.step)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
