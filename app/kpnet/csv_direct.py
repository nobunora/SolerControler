from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from app.kpnet.client import KpNetClient
from app.kpnet.client_support import clean_filename
from app.kpnet.config import KpNetConfig
from app.kpnet.csv_visualization import _plot_csvs, _resolve_months

LOGGER = logging.getLogger("app.kpnet.csv_direct")


def _open_measure_page(client: KpNetClient) -> tuple[list[str], str]:
    """Use the provider's proven two-step CSV navigation contract.

    The selector page itself does not contain the month list.  The month
    candidates live on the subsequent measure-output page as collectDate
    options.  This mirrors the contract that was known to work before the
    direct-HTTP hardening branch regressed the CSV path.
    """
    client._post(
        "remotevisualization/variousdataoutputselect",
        data={"_csrf": client.csrf_top},
        stage="csv-select",
    )
    measure = client._post(
        "remotevisualization/variousdataoutputselect/measureoutput",
        data={"_csrf": client.csrf_top},
        stage="csv-measure-page",
    )
    soup = BeautifulSoup(measure.text, "html.parser")
    months = [
        str(node.get("value", "")).strip()
        for node in soup.select("select[name='collectDate'] option")
        if str(node.get("value", "")).strip()
    ]
    if not months:
        raise RuntimeError("KP-NET CSV measure page contained no collectDate month options")

    pcsclass = "5"
    pcsclass_input = soup.select_one("input[name='pcsclass']")
    if pcsclass_input and pcsclass_input.get("value"):
        pcsclass = str(pcsclass_input.get("value", "")).strip() or "5"
    return months, pcsclass


def _download_csv(
    client: KpNetClient,
    cfg: KpNetConfig,
    *,
    month: str,
    pcsclass: str,
    out_dir: Path,
) -> Path:
    """Download one measurement CSV using the provider's proven form names."""
    response = client._post(
        "remotevisualization/variousdataoutputselect/measureoutput/download",
        data={
            "_csrf": client.csrf_top,
            "pcsclass": pcsclass,
            "outputFormat": cfg.csv_output_format,
            "aggrType": cfg.csv_aggr_type,
            "collectDate": month,
        },
        stage="csv-download",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    fallback = f"measure_{month.replace('-', '')}.csv"
    filename = clean_filename(client._response_filename(response, fallback=fallback))
    path = out_dir / filename
    path.write_bytes(response.content)
    return path


def run_csv_workflow() -> int:
    """Run the read-only KP-NET CSV phase with durable artifacts and diagnostics."""
    cfg = KpNetConfig.from_env()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = cfg.artifacts_dir / run_id
    csv_dir = run_dir / "csv"
    plot_path = run_dir / "kpi_plot.png"
    run_dir.mkdir(parents=True, exist_ok=True)

    client = KpNetClient(cfg)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "workflow_mode": "csv",
        "csv_downloads": [],
        "plot": {},
    }
    return_code = 1
    try:
        client.login()
        available_months, pcsclass = _open_measure_page(client)
        target_months = _resolve_months(
            requested=cfg.csv_target_months,
            available=available_months,
            include_latest=cfg.download_latest_month,
        )
        LOGGER.info("Available months: %s", available_months)
        LOGGER.info("Target months: %s", target_months)
        if not target_months:
            raise RuntimeError("KP-NET CSV target month set is empty after resolving available months")

        csv_paths: list[Path] = []
        for month in target_months:
            path = _download_csv(
                client,
                cfg,
                month=month,
                pcsclass=pcsclass,
                out_dir=csv_dir,
            )
            csv_paths.append(path)
            summary["csv_downloads"].append({"month": month, "path": str(path)})

        summary["plot"] = _plot_csvs(csv_paths, plot_path)
        return_code = 0
    except Exception as exc:
        LOGGER.exception("KP-NET direct CSV workflow failed")
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
    finally:
        try:
            client.logout()
        except Exception as exc:
            LOGGER.warning("KP-NET CSV logout failed: %s", type(exc).__name__)
        summary_path = run_dir / "kpnet_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        LOGGER.info("Summary saved: %s", summary_path)
    return return_code
