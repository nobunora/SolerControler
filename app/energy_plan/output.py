from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.energy_plan.models import PlanDocumentV1


@dataclass(frozen=True)
class EnergyPlanOutput:
    document: PlanDocumentV1
    output_path: Path

    def persist(self) -> None:
        payload = self.document.to_payload()
        payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
