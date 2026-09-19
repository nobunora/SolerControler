from __future__ import annotations

import pytest

from app.kpnet.realtime_soc_parser import extract_realtime_soc_percent_resilient


def test_resilient_soc_parser_accepts_battery_labels_without_icon_or_css_class() -> None:
    html = """
    <table>
      <tr><th rowspan="2">蓄電池</th><th>運転状態</th><th>蓄電残量</th></tr>
      <tr><td>充電</td><td>67 <span>%</span></td></tr>
    </table>
    """

    assert extract_realtime_soc_percent_resilient(html) == 67.0


def test_resilient_soc_parser_accepts_same_row_label_value_layout() -> None:
    html = """
    <table class="data_table_bt">
      <tr><th>蓄電池</th></tr>
      <tr><th>蓄電残量</th><td>81%</td></tr>
    </table>
    """

    assert extract_realtime_soc_percent_resilient(html) == 81.0


def test_resilient_soc_parser_does_not_take_percentage_from_non_battery_table() -> None:
    html = """
    <table><tr><th>蓄電残量</th><td>99%</td></tr></table>
    <table><tr><th>太陽光発電</th><td>44%</td></tr></table>
    """

    assert extract_realtime_soc_percent_resilient(html) is None


def test_resilient_soc_parser_rejects_out_of_range_soc() -> None:
    html = """
    <table><tr><th rowspan="2">蓄電池</th><th>蓄電残量</th></tr><tr><td>101%</td></tr></table>
    """

    with pytest.raises(ValueError, match="SOC out of range"):
        extract_realtime_soc_percent_resilient(html)
