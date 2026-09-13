from __future__ import annotations

from typing import Any

from app.operations.dashboard_plan_soc_replay import (
    build_missing_plan_soc_patch,
    candidate_from_forecast_plan,
    replay_missing_plan_soc_firestore,
)


def test_forecast_plan_before_07_is_valid_replay_evidence() -> None:
    candidate = candidate_from_forecast_plan(
        {
            "date": "2026-09-07",
            "forecast_run_id": "run-a",
            "forecast_issued_at": "2026-09-06T17:30:00Z",
            "planned_target_soc_percent": 72.0,
            "planned_night_charge_kwh": 3.2,
        }
    )

    assert candidate is not None
    assert candidate.date == "2026-09-07"
    assert candidate.target_soc_percent == 72.0
    assert candidate.night_charge_kwh == 3.2


def test_late_forecast_plan_is_not_accepted_as_historical_setting() -> None:
    candidate = candidate_from_forecast_plan(
        {
            "date": "2026-09-07",
            "updated_at": "2026-09-07T08:00:00+09:00",
            "planned_target_soc_percent": 72.0,
        }
    )

    assert candidate is None


def test_replay_patch_never_overwrites_existing_control_values() -> None:
    candidate = candidate_from_forecast_plan(
        {
            "date": "2026-09-07",
            "forecast_issued_at": "2026-09-06T17:30:00Z",
            "planned_target_soc_percent": 72.0,
            "planned_night_charge_kwh": 3.2,
        }
    )
    assert candidate is not None

    patch = build_missing_plan_soc_patch(
        {
            "setting_soc_target_percent": 80.0,
            "night_charge_kwh": 4.0,
            "settings_run_id": "strong-control-evidence",
        },
        candidate,
        replayed_at="2026-09-13T23:00:00+09:00",
    )

    assert patch == {}


def test_replay_patch_fills_only_missing_fields_with_provenance() -> None:
    candidate = candidate_from_forecast_plan(
        {
            "date": "2026-09-07",
            "forecast_run_id": "run-a",
            "forecast_issued_at": "2026-09-06T17:30:00Z",
            "planned_target_soc_percent": 72.0,
            "planned_night_charge_kwh": 3.2,
        }
    )
    assert candidate is not None

    patch = build_missing_plan_soc_patch(
        {"night_charge_kwh": 4.0},
        candidate,
        replayed_at="2026-09-13T23:00:00+09:00",
    )

    assert patch["setting_soc_target_percent"] == 72.0
    assert "night_charge_kwh" not in patch
    assert patch["plan_display_source"] == "forecast_plans_replay"
    assert patch["plan_replay_forecast_run_id"] == "run-a"


class _Doc:
    def __init__(self, collection: "_Collection", doc_id: str, data: dict[str, Any]) -> None:
        self._collection = collection
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def set(self, patch: dict[str, Any], merge: bool = False) -> None:
        assert merge is True
        current = self._collection.rows.setdefault(self.id, {})
        current.update(patch)


class _Query:
    def __init__(self, collection: "_Collection") -> None:
        self.collection = collection
        self.lower: str | None = None
        self.upper: str | None = None

    def where(self, field: str, operator: str, value: str) -> "_Query":
        assert field == "date"
        if operator == ">=":
            self.lower = value
        elif operator == "<=":
            self.upper = value
        else:
            raise AssertionError(operator)
        return self

    def stream(self) -> list[_Doc]:
        docs: list[_Doc] = []
        for doc_id, row in sorted(self.collection.rows.items()):
            day = str(row.get("date") or doc_id)
            if self.lower and day < self.lower:
                continue
            if self.upper and day > self.upper:
                continue
            docs.append(_Doc(self.collection, doc_id, row))
        return docs


class _Collection:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    def where(self, field: str, operator: str, value: str) -> _Query:
        return _Query(self).where(field, operator, value)

    def document(self, doc_id: str) -> _Doc:
        return _Doc(self, doc_id, self.rows.setdefault(doc_id, {}))


class _Client:
    def __init__(self) -> None:
        self.collections = {
            "forecast_plans": _Collection(
                {
                    "2026-09-06": {
                        "date": "2026-09-06",
                        "forecast_run_id": "run-06",
                        "forecast_issued_at": "2026-09-05T17:30:00Z",
                        "planned_target_soc_percent": 65.0,
                    },
                    "2026-09-07": {
                        "date": "2026-09-07",
                        "forecast_run_id": "late-run",
                        "forecast_issued_at": "2026-09-07T00:30:00Z",
                        "planned_target_soc_percent": 72.0,
                    },
                }
            ),
            "battery_daily_metrics": _Collection(
                {
                    "2026-09-05": {
                        "date": "2026-09-05",
                        "setting_soc_target_percent": 80.0,
                    }
                }
            ),
        }

    def collection(self, name: str) -> _Collection:
        return self.collections[name]


def test_replay_reports_recoverable_and_irrecoverable_gaps_and_is_idempotent() -> None:
    client = _Client()

    dry_run = replay_missing_plan_soc_firestore(
        client,
        start_date="2026-09-05",
        end_date="2026-09-07",
        apply=False,
        replayed_at="2026-09-13T23:00:00+09:00",
    )

    assert dry_run["missing_dates"] == ["2026-09-06", "2026-09-07"]
    assert dry_run["recoverable_dates"] == ["2026-09-06"]
    assert dry_run["irrecoverable_dates"] == ["2026-09-07"]
    assert dry_run["would_write"] == 1
    assert dry_run["skipped_untrusted"] == 1
    recoverable = next(item for item in dry_run["items"] if item["date"] == "2026-09-06")
    assert recoverable["forecast_run_id"] == "run-06"
    assert recoverable["action"] == "would_write"

    applied = replay_missing_plan_soc_firestore(
        client,
        start_date="2026-09-05",
        end_date="2026-09-07",
        apply=True,
        replayed_at="2026-09-13T23:00:00+09:00",
    )
    assert applied["written"] == 1
    assert client.collections["battery_daily_metrics"].rows["2026-09-06"][
        "setting_soc_target_percent"
    ] == 65.0

    second_dry_run = replay_missing_plan_soc_firestore(
        client,
        start_date="2026-09-05",
        end_date="2026-09-07",
        apply=False,
        replayed_at="2026-09-13T23:01:00+09:00",
    )
    assert second_dry_run["would_write"] == 0
    assert second_dry_run["irrecoverable_dates"] == ["2026-09-07"]
