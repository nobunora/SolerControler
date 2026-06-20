from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.utils import (
    clamp_percent,
    env,
    env_bool,
    env_float,
    env_float_clamped,
    env_int,
    load_dotenv_if_present,
    parse_csv_float,
    to_float,
    to_int,
)


def test_load_dotenv_if_present_sets_missing_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# comment",
                "PLAIN=value",
                "QUOTED=\"hello world\"",
                "SINGLE='single quoted'",
                "EXISTING=from_file",
                "EMPTY=",
                "NO_EQUALS",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING", "from_env")

    load_dotenv_if_present(env_path)

    assert os.environ["PLAIN"] == "value"
    assert os.environ["QUOTED"] == "hello world"
    assert os.environ["SINGLE"] == "single quoted"
    assert os.environ["EXISTING"] == "from_env"
    assert os.environ["EMPTY"] == ""


def test_env_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING", raising=False)
    monkeypatch.setenv("BOOL_TRUE", "yes")
    monkeypatch.setenv("BOOL_FALSE", "off")
    monkeypatch.setenv("BOOL_EMPTY", "")
    monkeypatch.setenv("INT_VALUE", "12")
    monkeypatch.setenv("FLOAT_VALUE", "1.25")

    assert env("MISSING", default="fallback") == "fallback"
    assert env_bool("BOOL_TRUE") is True
    assert env_bool("BOOL_FALSE", default=True) is False
    assert env_bool("BOOL_EMPTY", default=True) is True
    assert env_int("INT_VALUE") == 12
    assert env_int("MISSING", default=7) == 7
    assert env_float("FLOAT_VALUE") == pytest.approx(1.25)
    assert env_float("MISSING", default=2.5) == pytest.approx(2.5)
    with pytest.raises(ValueError):
        env("MISSING", required=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("1", 1.0),
        (" 1.5 ", 1.5),
        ("-2", -2.0),
        ("0", 0.0),
        (3, 3.0),
        (4.25, 4.25),
        (True, 1.0),
        (False, 0.0),
        ("1e2", 100.0),
        ("+7.5", 7.5),
        ("NaN", None),
        ("nan", None),
        ("inf", None),
        ("-inf", None),
        (float("nan"), None),
        (float("inf"), None),
        ("abc", None),
        ({}, None),
    ],
)
def test_to_float_patterns(raw: object, expected: float | None) -> None:
    result = to_float(raw)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_to_int_and_clamp_helpers() -> None:
    assert to_int("12.9") == 12
    assert to_int("bad") is None
    assert clamp_percent(-1.0) == 0.0
    assert clamp_percent(34.5) == 34.5
    assert clamp_percent(101.0) == 100.0
    assert parse_csv_float("bad", default=9.0) == 9.0
    assert parse_csv_float("1,234.5 kWh", default=None) == 1234.5
    assert parse_csv_float("bad", default=None) is None


def test_env_float_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAMPED_HIGH", "2.5")
    monkeypatch.setenv("CLAMPED_BAD", "bad")

    assert env_float_clamped("CLAMPED_HIGH", 0.5, min_val=0.0, max_val=1.0) == 1.0
    assert env_float_clamped("CLAMPED_MISSING", 0.5, min_val=0.0, max_val=1.0) == 0.5
    assert env_float_clamped("CLAMPED_BAD", 0.5, min_val=0.0, max_val=1.0) == 0.5
