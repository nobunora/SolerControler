from __future__ import annotations

import json
import os
import time
import uuid
from email.message import Message
from email.utils import collapse_rfc2231_value
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.kpnet.client_support import (
    extract_csrf as _extract_csrf,
    extract_error as _extract_error,
    extract_title as _extract_title,
)
from app.kpnet.config import KpNetConfig
from app.kpnet.profile_builder import _extract_simple_visualization_soc_percent


class KpNetUnknownWriteError(RuntimeError):
    """The provider may have accepted a settings write, but completion is unknown."""


class KpNetClient:
    def __init__(self, cfg: KpNetConfig, *, deadline_monotonic: float | None = None) -> None:
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/") + "/"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
                )
            }
        )
        self.deadline_monotonic = deadline_monotonic
        self.csrf_top = ""
        self.csrf_setting = ""
        self.pcsid = ""
        self.operation_id = uuid.uuid4().hex

    def _emit_http_event(
        self,
        *,
        stage: str,
        method: str,
        path: str,
        elapsed_ms: float,
        http_status: int | None = None,
        content_type: str | None = None,
        provider_status: object | None = None,
        exception_class: str | None = None,
        classification: str | None = None,
    ) -> None:
        """Best-effort, secret-free Cloud Logging telemetry at the HTTP boundary."""
        try:
            payload: dict[str, object] = {
                "message": "kpnet-http",
                "operation_id": getattr(self, "operation_id", None),
                "slot": os.getenv("CLOUD_JOB_SLOT", "").strip() or None,
                "stage": stage,
                "method": method,
                "endpoint": path.lstrip("/"),
                "elapsed_ms": round(elapsed_ms, 1),
                "task_attempt": os.getenv("CLOUD_RUN_TASK_ATTEMPT", "0").strip() or "0",
            }
            for key, value in {
                "http_status": http_status,
                "content_type": content_type,
                "provider_status": provider_status,
                "exception_class": exception_class,
                "classification": classification,
            }.items():
                if value is not None:
                    payload[key] = value
            print(json.dumps(payload, separators=(",", ":")), flush=True)
        except Exception:
            pass

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        base_url = getattr(self, "base_url", self.cfg.base_url)
        return urljoin(base_url, path.lstrip("/"))

    def _request_timeout(self) -> float:
        configured = float(self.cfg.timeout_sec)
        deadline = getattr(self, "deadline_monotonic", None)
        if deadline is None:
            return configured
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("KP-NET operation deadline exceeded")
        return max(0.001, min(configured, remaining))

    @staticmethod
    def _json_object(response: requests.Response, *, operation: str) -> dict[str, Any]:
        headers = getattr(response, "headers", {}) or {}
        content_type = headers.get("Content-Type", "")
        if content_type and "json" not in content_type.lower():
            raise RuntimeError(
                f"{operation} expected JSON but received content-type={content_type or 'unknown'}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"KP-NET {operation} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"KP-NET {operation} returned non-object JSON")
        return payload

    def _post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        stage: str | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        started = time.monotonic()
        stage = stage or f"post:{path.lstrip('/')}"
        try:
            resp = self.session.post(
                self._url(path), data=data, timeout=self._request_timeout(), **kwargs
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            self._emit_http_event(
                stage=stage,
                method="POST",
                path=path,
                elapsed_ms=(time.monotonic() - started) * 1000,
                exception_class=type(exc).__name__,
                classification="unknown_write" if "write/" in path else "failed",
            )
            raise
        self._emit_http_event(
            stage=stage,
            method="POST",
            path=path,
            elapsed_ms=(time.monotonic() - started) * 1000,
            http_status=getattr(resp, "status_code", None),
            content_type=getattr(resp, "headers", {}).get("Content-Type"),
        )
        return resp

    def _get(self, path: str, *, stage: str | None = None, **kwargs: Any) -> requests.Response:
        started = time.monotonic()
        stage = stage or f"get:{path.lstrip('/')}"
        try:
            resp = self.session.get(self._url(path), timeout=self._request_timeout(), **kwargs)
            resp.raise_for_status()
        except requests.RequestException as exc:
            self._emit_http_event(
                stage=stage,
                method="GET",
                path=path,
                elapsed_ms=(time.monotonic() - started) * 1000,
                exception_class=type(exc).__name__,
                classification="failed",
            )
            raise
        self._emit_http_event(
            stage=stage,
            method="GET",
            path=path,
            elapsed_ms=(time.monotonic() - started) * 1000,
            http_status=getattr(resp, "status_code", None),
            content_type=getattr(resp, "headers", {}).get("Content-Type"),
        )
        return resp

    def login(self) -> None:
        page = self._get("login", stage="login-page")
        csrf = _extract_csrf(page.text)
        response = self._post(
            "processLogin",
            data={"_csrf": csrf, "loginid": self.cfg.username, "loginpassword": self.cfg.password},
            allow_redirects=True,
            stage="login-submit",
        )
        response_url = str(getattr(response, "url", "") or "")
        response_url_lower = response_url.lower()
        if response_url and "login" in response_url_lower and "processlogin" not in response_url_lower:
            raise RuntimeError("KP-NET login failed")
        top = self._get("remotevisualization/simplevisualization/enduser", stage="login-top")
        self.csrf_top = _extract_csrf(top.text)
        if not self.csrf_top:
            raise RuntimeError("KP-NET login/session validation failed")

    def logout(self) -> None:
        try:
            self._get("logout", stage="logout")
        finally:
            close_session = getattr(self.session, "close", None)
            if callable(close_session):
                close_session()

    def read_realtime_soc_percent(self) -> float | None:
        html = self._get(
            "remotevisualization/simplevisualization/enduser", stage="realtime-soc"
        ).text
        return _extract_simple_visualization_soc_percent(html)

    def open_csv_measure_page(self) -> tuple[list[str], str]:
        response = self._post(
            "remotevisualization/variousdataoutputselect",
            data={"_csrf": self.csrf_top},
            stage="csv-select",
        )
        soup = BeautifulSoup(response.text, "html.parser")
        available: list[str] = []
        for node in soup.select("select option"):
            value = str(node.get("value", "")).strip()
            if value and value not in available:
                available.append(value)
        pcsclass_node = soup.select_one("input[name='pcsclass']")
        pcsclass = str(pcsclass_node.get("value", "")) if pcsclass_node else ""
        return available, pcsclass

    def download_csv(self, *, month: str, pcsclass: str, out_dir: Path) -> Path:
        measure = self._post(
            "remotevisualization/variousdataoutputselect/measureoutput",
            data={"_csrf": self.csrf_top, "measuremonth": month, "pcsclass": pcsclass},
            stage="csv-measure",
        )
        download = self._post(
            "remotevisualization/variousdataoutputselect/measureoutput/download",
            data={"_csrf": _extract_csrf(measure.text), "measuremonth": month, "pcsclass": pcsclass},
            stage="csv-download",
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = self._response_filename(download, fallback=f"kpnet-{month}.csv")
        path = out_dir / filename
        path.write_bytes(download.content)
        return path

    @staticmethod
    def _response_filename(response: requests.Response, *, fallback: str) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        if not disposition:
            return fallback
        message = Message()
        message["Content-Disposition"] = disposition
        filename = message.get_param("filename", header="Content-Disposition")
        if filename is None:
            return fallback
        if isinstance(filename, tuple):
            return str(collapse_rfc2231_value(filename))
        return str(filename)

    def _poll_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
        max_wait_sec: float = 60.0,
    ) -> dict[str, Any]:
        start = time.time()
        classification = "unknown_write" if "write/" in path else "failed"
        while time.time() - start < max_wait_sec:
            resp = self._post(path, data=payload, headers=headers)
            try:
                data = self._json_object(resp, operation=path)
            except RuntimeError as exc:
                self._emit_http_event(
                    stage=f"poll:{path.lstrip('/')}:terminal",
                    method="POST",
                    path=path,
                    elapsed_ms=0.0,
                    exception_class=type(exc).__name__,
                    classification=classification,
                )
                raise
            self._emit_http_event(
                stage=f"poll:{path.lstrip('/')}",
                method="POST",
                path=path,
                elapsed_ms=0.0,
                provider_status=data.get("status"),
                classification="complete" if data.get("status") == 1 else "pending",
            )
            if data.get("status") == 1:
                return data
            deadline = getattr(self, "deadline_monotonic", None)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.6, remaining))
            else:
                time.sleep(0.6)
        self._emit_http_event(
            stage=f"poll:{path.lstrip('/')}:terminal",
            method="POST",
            path=path,
            elapsed_ms=0.0,
            exception_class="TimeoutError",
            classification=classification,
        )
        raise TimeoutError(f"Polling timeout: {path}")

    def open_settings_page(self) -> None:
        gw = self._post(
            "remotesetting/gwpcsmanage",
            data={"_csrf": self.csrf_top},
            stage="settings-gateway",
        )
        soup = BeautifulSoup(gw.text, "html.parser")
        pcs_node = soup.select_one(
            "form[action='/settingcontrol/remotesetting/pcsselect/pcs'] button[name='pcsid'], "
            "button[name='pcsid'], input[name='pcsid'], select[name='pcsid'] option"
        )
        if pcs_node is None or not pcs_node.get("value"):
            raise RuntimeError("KP-NET pcsid not found")
        self.pcsid = str(pcs_node.get("value", "")).strip()
        if not self.pcsid:
            raise RuntimeError("KP-NET pcsid is empty")

        try:
            gateway_csrf = _extract_csrf(gw.text)
        except RuntimeError:
            gateway_csrf = self.csrf_top
        select = self._post(
            "remotesetting/pcsselect/pcs",
            data={"_csrf": gateway_csrf, "pcsid": self.pcsid},
            stage="settings-pcs-select",
        )
        try:
            select_csrf = _extract_csrf(select.text)
        except RuntimeError:
            select_csrf = gateway_csrf
        settings = self._post(
            "remotesetting/pcssetting",
            data={"_csrf": select_csrf, "pcsid": self.pcsid, "pcsCategory": "BatterySetting"},
            stage="settings-page",
        )
        self.csrf_setting = _extract_csrf(settings.text)
        if not self.csrf_setting:
            raise RuntimeError("KP-NET settings CSRF token not found")

    def _ajax_headers(self) -> dict[str, str]:
        return {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": self.csrf_setting,
            "Referer": self._url("remotesetting/pcssetting"),
        }

    def read_current_settings(self) -> dict[str, Any]:
        headers = self._ajax_headers()
        request = self._post(
            "remotesetting/pcssetting/read/request",
            data={
                "_csrf": self.csrf_setting,
                "pcsCategory": "BatterySetting",
                "pcsid": self.pcsid,
            },
            headers=headers,
            stage="settings-read-request",
        )
        req = self._json_object(request, operation="settings read request")
        comm = req.get("data", {})
        result = self._poll_json(
            "remotesetting/pcssetting/read/response",
            {
                "communicationSequenceno": comm.get("communicationSequenceno", ""),
                "value": comm.get("value", ""),
            },
            headers=headers,
        )
        data = result.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("settings read response did not contain an object")
        return data

    def candidate_map(self, candidate_type: str, value_list_path: str) -> dict[str, str]:
        headers = self._ajax_headers()
        request = self._post(
            "remotesetting/pcssetting/read/request/candidate",
            data={"candidateType": candidate_type},
            headers=headers,
            stage=f"settings-candidate-request:{candidate_type}",
        )
        req = self._json_object(request, operation="candidate read request")
        comm = req.get("data", {})
        self._poll_json(
            "remotesetting/pcssetting/read/response/candidate",
            {
                "communicationSequenceno": comm.get("communicationSequenceno", ""),
                "value": comm.get("value", ""),
            },
            headers=headers,
        )
        response = self._post(
            value_list_path,
            headers=headers,
            stage=f"settings-value-list:{candidate_type}",
        )
        payload = self._json_object(response, operation="candidate value list")
        result: dict[str, str] = {}
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError(f"candidate value list was not an array: {candidate_type}")
        for item in data:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code", ""))
            value = str(item.get("value", ""))
            if code:
                result[code] = value
        return result

    def collect_candidate_maps(self) -> dict[str, dict[str, str]]:
        targets = {
            "BatteryOperatingMode": "remotesetting/pcssetting/valueList/batteryoperatingmode",
            "SocSafetyMode": "remotesetting/pcssetting/valueList/socsafetymode",
            "SocEconomyMode": "remotesetting/pcssetting/valueList/soceconomymode",
            "SocContactInput": "remotesetting/pcssetting/valueList/soccontactinput",
            "SocChargeMode": "remotesetting/pcssetting/valueList/socchargemode",
            "OnPowerOutageChargePowerW": "remotesetting/pcssetting/valueList/onpoweroutagechargepower",
            "AgreementAmpere": "remotesetting/pcssetting/valueList/agreementampere",
        }
        return {key: self.candidate_map(key, path) for key, path in targets.items()}

    def confirm_setting(self, payload: dict[str, str]) -> tuple[bool, str, str, str]:
        response = self._post(
            "remotesetting/pcssettingconfirm/batterysetting",
            data=payload,
            stage="settings-confirm",
        )
        title = _extract_title(response.text)
        error = _extract_error(response.text)
        ok = not error and "id=\"pcs-input-complete\"" in response.text
        return ok, title, error, response.text

    def _extract_form_data(self, html: str) -> tuple[dict[str, str], str]:
        soup = BeautifulSoup(html, "html.parser")
        csrf = _extract_csrf(html)
        form = soup.select_one("form#itemForm, form#ItemForm")
        if form is None:
            raise RuntimeError("確認画面フォーム(ItemForm)を取得できませんでした")

        data: dict[str, str] = {}
        for input_node in form.select("input[name]"):
            if input_node.has_attr("disabled"):
                continue
            name = str(input_node.get("name", "")).strip()
            if not name:
                continue
            data[name] = str(input_node.get("value", ""))

        for select_node in form.select("select[name]"):
            if select_node.has_attr("disabled"):
                continue
            name = str(select_node.get("name", "")).strip()
            if not name:
                continue
            selected = select_node.select_one("option[selected]") or select_node.select_one("option")
            data[name] = str(selected.get("value", "")) if selected else ""

        data["_csrf"] = csrf
        return data, csrf

    def write_setting(self, confirm_html: str) -> dict[str, Any]:
        form_data, csrf = self._extract_form_data(confirm_html)
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": csrf,
            "Referer": self._url("remotesetting/pcssettingconfirm/batterysetting"),
        }

        try:
            req_response = self._post(
                "remotesetting/pcssetting/write/request", data=form_data, headers=headers
            )
            req = self._json_object(req_response, operation="settings write request")
            comm = req.get("data", {})
            self._poll_json(
                "remotesetting/pcssetting/write/response",
                {
                    "communicationSequenceno": comm.get("communicationSequenceno", ""),
                    "value": comm.get("value", ""),
                },
                headers=headers,
                max_wait_sec=90.0,
            )
            self._post("remotesetting/pcssettingcomplete/", data={"_csrf": csrf})
            self._post("remotesetting/pcssetting/write/requestdevicedetail", headers=headers)
        except (requests.RequestException, RuntimeError, TimeoutError) as exc:
            self._emit_http_event(
                stage="settings-write-terminal",
                method="POST",
                path="remotesetting/pcssetting/write",
                elapsed_ms=0.0,
                exception_class=type(exc).__name__,
                classification="unknown_write",
            )
            raise KpNetUnknownWriteError("KP-NET settings write outcome is unknown") from exc

        return {"changed": True}
