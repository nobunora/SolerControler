import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.forecasting.pv_ideal_max import HourlyPvObservation, IdealMaxArray, IdealMaxGeometry, ideal_max_base
from app.forecasting.pv_ideal_max_store import IdealMaxStore
from scripts.prepare_pv_geometry_migration import prepare
from scripts.replay_pv_bias_training import replay


def test_geometry_rebuild_preserves_source_and_issues_no_past_predictions(tmp_path, monkeypatch):
    zone = ZoneInfo("Asia/Tokyo")
    start = datetime(2026, 10, 1, 12, tzinfo=zone)
    source = IdealMaxGeometry(35., 139., "Asia/Tokyo", (IdealMaxArray(0, 20, 1., .82, 1.),))
    target = replace(source, arrays=tuple(replace(a, capacity_kw=1.08, tilt_deg=19) for a in source.arrays))
    monkeypatch.setattr("scripts.prepare_pv_geometry_migration.configured_ideal_max_geometry", lambda: target)
    config = tmp_path / "source-arrays.json"
    config.write_text(json.dumps({"arrays": [asdict(a) for a in source.arrays]}), encoding="utf-8")
    with IdealMaxStore(tmp_path / "old", source, initialize_at=start - timedelta(days=1)) as old:
        actual = ideal_max_base(start.date(), 12, source) * 2
        old.ingest(HourlyPvObservation(start, actual / 2, actual / 2, start + timedelta(hours=1)), as_of=start + timedelta(hours=1))
        path = old.path
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = prepare(source_arrays=config, output=tmp_path / "migration", source_ledger=path)
    assert result["source_reproduced"] and result["observations"] == 1
    assert result["past_predictions_issued"] == 0
    assert result["target_fingerprint"] != source.fingerprint
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with pytest.raises(ValueError, match="exists"):
        prepare(source_arrays=config, output=tmp_path / "migration", source_ledger=path)


def test_local_nonapplying_tool_refuses_existing_output_before_any_input_read(tmp_path):
    with pytest.raises(ValueError, match="exists"):
        replay(database=tmp_path / "missing.db", originals_directory=tmp_path / "missing", ledger=tmp_path / "missing.sqlite",
               target_date="2026-11-01", as_of=datetime(2026, 11, 1, tzinfo=ZoneInfo("Asia/Tokyo")), output=tmp_path)
