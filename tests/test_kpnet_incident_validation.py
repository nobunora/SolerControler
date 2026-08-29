from __future__ import annotations

import ast
from pathlib import Path


def test_incident_validator_is_not_a_scheduled_control_dependency() -> None:
    slots = Path("app/runtime/slot_orchestration.py").read_text(encoding="utf-8")
    assert "kpnet_incident_validation" not in slots
    tree = ast.parse(slots)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "Firestore" not in names
