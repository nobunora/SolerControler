from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.kpnet.csv_direct import _download_csv, _open_measure_page


class _Response:
    def __init__(self, text: str = "", *, content: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.text = text
        self.content = content
        self.headers = headers or {}


class _Client:
    def __init__(self) -> None:
        self.csrf_top = "csrf-top"
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _post(self, path: str, data: dict[str, object] | None = None, **_: object) -> _Response:
        self.calls.append((path, dict(data or {})))
        if path.endswith("variousdataoutputselect"):
            return _Response("<html><body>selector-only</body></html>")
        if path.endswith("measureoutput"):
            return _Response(
                """
                <html><body>
                  <input name="pcsclass" value="5">
                  <select name="collectDate">
                    <option value="2026-08">2026-08</option>
                    <option value="2026-09">2026-09</option>
                  </select>
                </body></html>
                """
            )
        if path.endswith("measureoutput/download"):
            return _Response(
                content=b"timestamp,soc\n2026-09-17T03:00,0\n",
                headers={"Content-Disposition": 'attachment; filename="measure.csv"'},
            )
        raise AssertionError(path)

    @staticmethod
    def _response_filename(response: _Response, *, fallback: str) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        if "measure.csv" in disposition:
            return "measure.csv"
        return fallback


def test_csv_months_are_read_from_measure_output_page_not_selector_page() -> None:
    client = _Client()

    months, pcsclass = _open_measure_page(client)  # type: ignore[arg-type]

    assert months == ["2026-08", "2026-09"]
    assert pcsclass == "5"
    assert [path for path, _ in client.calls] == [
        "remotevisualization/variousdataoutputselect",
        "remotevisualization/variousdataoutputselect/measureoutput",
    ]


def test_csv_download_uses_proven_provider_form_contract(tmp_path: Path) -> None:
    client = _Client()
    cfg = SimpleNamespace(csv_output_format="太陽光発電＋蓄電池", csv_aggr_type="30分データ")

    path = _download_csv(  # type: ignore[arg-type]
        client,
        cfg,
        month="2026-09",
        pcsclass="5",
        out_dir=tmp_path,
    )

    assert path.read_bytes().startswith(b"timestamp,soc")
    request_path, payload = client.calls[-1]
    assert request_path == "remotevisualization/variousdataoutputselect/measureoutput/download"
    assert payload == {
        "_csrf": "csrf-top",
        "pcsclass": "5",
        "outputFormat": "太陽光発電＋蓄電池",
        "aggrType": "30分データ",
        "collectDate": "2026-09",
    }
