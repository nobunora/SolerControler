from __future__ import annotations

import time
from email.message import Message
from email.utils import collapse_rfc2231_value
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.kpnet.config import LOGGER, KpNetConfig
from app.kpnet.client_support import (
    clean_filename as _clean_filename,
    extract_alert_message as _extract_alert_message,
    extract_csrf as _extract_csrf,
    extract_title as _extract_title,
)
from app.kpnet.profile_builder import _extract_simple_visualization_soc_percent
class KpNetClient:
    def __init__(self, cfg: KpNetConfig) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.0.0 Safari/537.36"
                )
            }
        )
        self.csrf_top = ""
        self.csrf_setting = ""
        self.pcsid = ""

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.cfg.base_url.rstrip("/") + "/", path.lstrip("/"))

    def _ajax_headers(self, referer_path: str) -> dict[str, str]:
        return {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": self.csrf_setting,
            "Referer": self._url(referer_path),
        }

    @staticmethod
    def _json_object(response: requests.Response, *, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"KP-NET {operation} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"KP-NET {operation} returned a non-object JSON payload")
        return payload

    # readable-code-audit: skip NAME-02 — kwargs are deliberately passed through to requests for provider-specific HTTP options
    def _post(self, path: str, data: dict[str, Any] | None = None, **kwargs: Any) -> requests.Response:
        resp = self.session.post(
            self._url(path),
            data=data,
            timeout=self.cfg.timeout_sec,
            **kwargs,
        )
        resp.raise_for_status()
        return resp

    def _get(self, path: str, **kwargs: Any) -> requests.Response:
        resp = self.session.get(
            self._url(path),
            timeout=self.cfg.timeout_sec,
            **kwargs,
        )
        resp.raise_for_status()
        return resp

    def login(self) -> None:
        login_page = self._get("login")
        csrf = _extract_csrf(login_page.text)
        self._post(
            "processLogin",
            data={
                "_csrf": csrf,
                "loginid": self.cfg.username,
                "loginpassword": self.cfg.password,
            },
        )

        top = self._get("remotevisualization/simplevisualization/enduser")
        self.csrf_top = _extract_csrf(top.text)
        if "ログイン" in _extract_title(top.text) and "ユーザID" in top.text:
            raise RuntimeError("ログインに失敗しました。ユーザIDまたはパスワードをご確認ください。")
        LOGGER.info("Login success")

    def read_realtime_soc_percent(self) -> float | None:
        resp = self._get("remotevisualization/simplevisualization/enduser")
        return _extract_simple_visualization_soc_percent(resp.text)

    def logout(self) -> None:
        csrf = self.csrf_setting or self.csrf_top
        if not csrf:
            return
        self._post("logout", data={"_csrf": csrf})
        LOGGER.info("Logout success")

    def open_csv_measure_page(self) -> tuple[list[str], str]:
        self._post("remotevisualization/variousdataoutputselect", data={"_csrf": self.csrf_top})
        measure = self._post(
            "remotevisualization/variousdataoutputselect/measureoutput",
            data={"_csrf": self.csrf_top},
        )
        soup = BeautifulSoup(measure.text, "html.parser")
        month_options = [
            str(node.get("value", "")).strip()
            for node in soup.select("select[name='collectDate'] option")
            if str(node.get("value", "")).strip()
        ]
        pcsclass = "5"
        pcsclass_input = soup.select_one("input[name='pcsclass']")
        if pcsclass_input and pcsclass_input.get("value"):
            pcsclass = str(pcsclass_input["value"]).strip()
        return month_options, pcsclass

    def download_csv(self, month: str, pcsclass: str, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        resp = self._post(
            "remotevisualization/variousdataoutputselect/measureoutput/download",
            data={
                "_csrf": self.csrf_top,
                "pcsclass": pcsclass,
                "outputFormat": self.cfg.csv_output_format,
                "aggrType": self.cfg.csv_aggr_type,
                "collectDate": month,
            },
        )
        disp = resp.headers.get("Content-Disposition", "")
        msg = Message()
        if disp:
            msg["Content-Disposition"] = disp
        filename = msg.get_param("filename", header="Content-Disposition")
        if not filename:
            filename = f"measure_{month.replace('-', '')}.csv"
        elif isinstance(filename, tuple):
            filename = collapse_rfc2231_value(filename)
        path = out_dir / _clean_filename(filename)
        path.write_bytes(resp.content)
        LOGGER.info("CSV downloaded month=%s path=%s", month, path)
        return path

    def open_settings_page(self) -> None:
        gw = self._post("remotesetting/gwpcsmanage", data={"_csrf": self.csrf_top})
        soup = BeautifulSoup(gw.text, "html.parser")
        pcs_btn = soup.select_one("form[action='/settingcontrol/remotesetting/pcsselect/pcs'] button[name='pcsid']")
        if not pcs_btn or not pcs_btn.get("value"):
            raise RuntimeError("pcsid を取得できませんでした")
        self.pcsid = str(pcs_btn["value"]).strip()

        self._post("remotesetting/pcsselect/pcs", data={"_csrf": self.csrf_top, "pcsid": self.pcsid})
        setting = self._post(
            "remotesetting/pcssetting",
            data={"_csrf": self.csrf_top, "pcsid": self.pcsid, "pcsCategory": "BatterySetting"},
        )
        self.csrf_setting = _extract_csrf(setting.text)
        LOGGER.info("Settings page opened pcsid=%s", self.pcsid)

    def _poll_json(
        self,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        max_wait_sec: float = 60.0,
    ) -> dict[str, Any]:
        start = time.time()
        while time.time() - start < max_wait_sec:
            resp = self._post(path, data=payload, headers=headers)
            data = self._json_object(resp, operation=path)
            if data.get("status") == 1:
                return data
            time.sleep(0.6)
        raise TimeoutError(f"Polling timeout: {path}")

    def read_current_settings(self) -> dict[str, Any]:
        headers = self._ajax_headers("remotesetting/pcssetting")
        req_response = self._post(
            "remotesetting/pcssetting/read/request",
            data={"_csrf": self.csrf_setting, "pcsCategory": "BatterySetting", "pcsid": self.pcsid},
            headers=headers,
        )
        req = self._json_object(req_response, operation="settings read request")
        comm = req.get("data", {})
        result = self._poll_json(
            "remotesetting/pcssetting/read/response",
            {"communicationSequenceno": comm.get("communicationSequenceno", ""), "value": comm.get("value", "")},
            headers=headers,
        )
        data = result.get("data", {})
        return data if isinstance(data, dict) else {}

    def candidate_map(self, candidate_type: str, value_list_path: str) -> dict[str, str]:
        headers = self._ajax_headers("remotesetting/pcssetting")
        req_response = self._post(
            "remotesetting/pcssetting/read/request/candidate",
            data={"candidateType": candidate_type},
            headers=headers,
        )
        req = self._json_object(req_response, operation="candidate read request")
        comm = req.get("data", {})
        self._poll_json(
            "remotesetting/pcssetting/read/response/candidate",
            {"communicationSequenceno": comm.get("communicationSequenceno", ""), "value": comm.get("value", "")},
            headers=headers,
        )
        list_response = self._post(
            value_list_path,
            headers=headers,
        )
        list_resp = self._json_object(list_response, operation="candidate value list")
        result: dict[str, str] = {}
        for item in list_resp.get("data", []):
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
        return {k: self.candidate_map(k, v) for k, v in targets.items()}

    def confirm_setting(self, payload: dict[str, str]) -> tuple[bool, str, str, str]:
        resp = self._post("remotesetting/pcssettingconfirm/batterysetting", data=payload)
        html = resp.text
        title = _extract_title(html)
        err = _extract_alert_message(html)
        has_complete_button = "id=\"pcs-input-complete\"" in html
        return has_complete_button, title, err, html

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

        req_response = self._post("remotesetting/pcssetting/write/request", data=form_data, headers=headers)
        req = self._json_object(req_response, operation="settings write request")
        comm = req.get("data", {})
        self._poll_json(
            "remotesetting/pcssetting/write/response",
            {"communicationSequenceno": comm.get("communicationSequenceno", ""), "value": comm.get("value", "")},
            headers=headers,
            max_wait_sec=90.0,
        )

        self._post("remotesetting/pcssettingcomplete/", data={"_csrf": csrf})
        self._post("remotesetting/pcssetting/write/requestdevicedetail", headers=headers)
        return {"changed": True}


# readable-code-audit: skip STRUCT-04 — payload fields are serialized as one provider request contract and cannot be independently emitted
