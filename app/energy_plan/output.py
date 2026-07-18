from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.energy_plan.models import PlanDocumentV1


@dataclass(frozen=True)
class EnergyPlanOutput:
    document: PlanDocumentV1
    output_path: Path

    def persist(self) -> None:
        self.output_path.write_text(
            json.dumps(self.document.to_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
