from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.domain.constants import validate_soc_percent

_SOC_VALUE = re.compile(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*%?\s*")


def _parse_soc_text(raw_value: str) -> float | None:
    match = _SOC_VALUE.fullmatch(raw_value)
    if not match:
        return None
    return validate_soc_percent(float(match.group(1)), raw=raw_value)


def extract_realtime_soc_percent_resilient(html: str) -> float | None:
    """Extract battery SOC without depending on KP-NET presentation-only CSS/icons.

    The primary parser historically keyed on ``table.data_table_bt`` plus a Font
    Awesome battery icon.  Those are presentation details and can change while the
    semantic labels remain stable.  This fallback accepts only tables that clearly
    identify both the battery (``蓄電池`` or a battery icon) and the SOC header
    (``蓄電残量``), so loosening the markup dependency does not loosen the semantic
    identity of the value being read.
    """

    soup = BeautifulSoup(html, "html.parser")
    tables = list(soup.select("table.data_table_bt"))
    if not tables:
        tables = list(soup.select("table"))

    for table in tables:
        table_text = table.get_text(" ", strip=True)
        has_battery_icon = table.select_one('[class*="fa-battery-"]') is not None
        has_battery_label = "蓄電池" in table_text
        if not (has_battery_icon or has_battery_label) or "蓄電残量" not in table_text:
            continue

        value_headers = []
        for header in table.select("th"):
            header_text = header.get_text(" ", strip=True)
            if header.select_one('[class*="fa-battery-"]') is not None:
                continue
            if "蓄電池" in header_text:
                continue
            value_headers.append(header)

        soc_column = next(
            (
                index
                for index, header in enumerate(value_headers)
                if "蓄電残量" in header.get_text(" ", strip=True)
            ),
            None,
        )
        if soc_column is not None:
            for row in table.select("tr"):
                cells = row.select("td")
                if soc_column >= len(cells):
                    continue
                value = _parse_soc_text(cells[soc_column].get_text(" ", strip=True))
                if value is not None:
                    return value

        # Some KP-NET layouts render a label/value pair in the same row instead
        # of a columnar header.  Keep this fallback semantic: only a cell directly
        # associated with the 蓄電残量 label is considered.
        for row in table.select("tr"):
            nodes = row.find_all(["th", "td"], recursive=False)
            for index, node in enumerate(nodes[:-1]):
                if "蓄電残量" not in node.get_text(" ", strip=True):
                    continue
                value = _parse_soc_text(nodes[index + 1].get_text(" ", strip=True))
                if value is not None:
                    return value

    return None
