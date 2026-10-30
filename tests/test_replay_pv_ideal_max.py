"""CSV aggregation and runnable replay without weather or production writes."""

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts import replay_pv_ideal_max as cli

START = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def samples(first=0.0, second=0.0):
    return [{"dt": START.replace(tzinfo=None), "pv": first},
            {"dt": (START + timedelta(minutes=30)).replace(tzinfo=None), "pv": second}]


def aggregate(rows, **kwargs):
    return cli.hourly_observations(rows, zone=ZoneInfo("UTC"), as_of=START + timedelta(hours=1),
                                   hours=(0, 24), replay=True, **kwargs)


def test_zero_is_valid_missing_is_not_filled_and_duplicates_do_not_double_count():
    observations, counts = aggregate(samples() + samples())
    assert observations[0].actual_kwh == 0
    assert counts["duplicate_halfhours"] == 2
    assert aggregate(samples(None, 1))[0] == []
    assert aggregate(samples()[:1])[1]["incomplete_hours"] == 1
    with pytest.raises(ValueError, match="conflicting"):
        aggregate(samples() + samples(1, 0))


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True])
def test_invalid_actual_is_rejected(value):
    with pytest.raises(ValueError):
        aggregate(samples(value, 0))


def test_future_and_explicit_hours_and_import_availability():
    rows = samples(0.4, 0.6)
    obs, counts = cli.hourly_observations(rows, zone=ZoneInfo("UTC"), as_of=START,
                                         hours=(0, 24), replay=True)
    assert obs == [] and counts["future_hours"] == 1
    obs, counts = cli.hourly_observations(rows, zone=ZoneInfo("UTC"), as_of=START + timedelta(days=1),
                                         hours=(5, 12), replay=True)
    assert obs == [] and counts["outside_learning_hours"] == 1
    obs, _ = cli.hourly_observations(rows, zone=ZoneInfo("UTC"), as_of=START + timedelta(days=1),
                                    hours=(0, 24), replay=False)
    assert obs[0].available_at == START + timedelta(days=1)


def test_real_csv_reader_replay_restart_and_no_output_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.EnergyModelConfig, "from_env", lambda: SimpleNamespace(latitude=35, longitude=135, timezone="UTC"))
    monkeypatch.setattr(cli, "load_pv_array_configs", lambda: [SimpleNamespace(
        azimuth_deg=180, tilt_deg=20, capacity_kw=2, performance_ratio=0.8, shading_factor=1)])
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    with (csv_dir / "actual.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["年月日", "時刻", "発電電力量[kWh]", "買電電力量[kWh]", "売電電力量[kWh]"])
        writer.writerows([["2026/06/01", "03:00", "0", "0", "0"], ["2026/06/01", "03:30", "0", "0", "0"]])
    args = argparse.Namespace(csv_dir=csv_dir, state_dir=tmp_path / "state", output=tmp_path / "first.json",
                              as_of=START, target_date=date(2026, 6, 2), learning_hours="0:24", initialize=True, replay=True)
    first = cli.run(args)
    assert first["available_hours"] == 1
    assert first["new_observations"][0]["issued_prediction"] is not None
    assert len(first["hourly_maximum"]) == 24
    assert len({p["coefficient"] for p in first["hourly_maximum"]}) == 1
    with pytest.raises(FileExistsError):
        cli.run(args)
    args.output = tmp_path / "second.json"
    args.initialize = False
    second = cli.run(args)
    assert second["duplicate_hours"] == 1
    assert second["revision"] == first["revision"]
    assert second["new_observations"] == []
