from __future__ import annotations

from app.postgres_ops import recalc_cost_daily


class _Cursor:
    def __init__(self) -> None:
        self.rows = [{"ts": "2026-05-01T07:00:00+09:00", "load_kwh": 2.0, "buy_kwh": 0.5}]
        self.writes: list[tuple[object, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, query: str, params=None):
        if params is not None:
            self.writes.append(tuple(params))
        return self

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self) -> None:
        self.cursor_value = _Cursor()
        self.commits = 0

    def cursor(self) -> _Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1


def test_postgres_daily_cost_maps_domain_result_to_existing_columns() -> None:
    connection = _Connection()

    recalc_cost_daily(
        connection,
        day_rate_yen_per_kwh=31.0,
        updated_at="2026-05-02T00:00:00Z",
    )

    assert connection.cursor_value.writes == [
        ("2026-05-01", 1.5, 46.5, 1.5, 46.5, "2026-05-02T00:00:00Z")
    ]
    assert connection.commits == 1
