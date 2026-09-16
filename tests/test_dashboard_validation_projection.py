from app.operations.dashboard_validation_projection import _projection_candidates


def _forecast_rows(
    day: str,
    *,
    target: float | None = 100.0,
    night_charge: float | None = 9.5,
    reconstructed: bool = False,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for hour in range(24):
        row: dict[str, object] = {
            "date": day,
            "hour": hour,
            "source": "historical_reconstructed_estimate" if reconstructed else "forecast_hourly",
            "is_reconstructed": reconstructed,
        }
        if target is not None:
            row["forecast_target_soc_percent"] = target
        if night_charge is not None:
            row["forecast_night_charge_kwh"] = night_charge
        rows.append(row)
    return rows


def test_projection_creates_missing_display_only_battery_day() -> None:
    candidates = _projection_candidates([], _forecast_rows("2026-09-14"))

    assert candidates == [
        {
            "date": "2026-09-14",
            "setting_soc_target_percent": 100.0,
            "night_charge_kwh": 9.5,
        }
    ]


def test_projection_never_overwrites_existing_control_values() -> None:
    existing = [
        {
            "date": "2026-09-14",
            "setting_soc_target_percent": 85.0,
            "night_charge_kwh": 4.0,
        }
    ]

    assert _projection_candidates(existing, _forecast_rows("2026-09-14")) == []


def test_projection_fills_only_missing_field() -> None:
    existing = [
        {
            "date": "2026-09-14",
            "setting_soc_target_percent": 85.0,
            "night_charge_kwh": None,
        }
    ]

    assert _projection_candidates(existing, _forecast_rows("2026-09-14")) == [
        {
            "date": "2026-09-14",
            "setting_soc_target_percent": None,
            "night_charge_kwh": 9.5,
        }
    ]


def test_projection_rejects_reconstructed_forecast_rows() -> None:
    assert _projection_candidates([], _forecast_rows("2026-09-14", reconstructed=True)) == []
