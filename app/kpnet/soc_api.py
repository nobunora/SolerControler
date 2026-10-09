"""Fresh SOC measurements from the KP-NET mobile remote API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import json
import os
import secrets
import time
from typing import Any
from zoneinfo import ZoneInfo

import requests


JST = ZoneInfo("Asia/Tokyo")


class SocApiError(RuntimeError):
    """A deliberately secret-free provider or measurement failure."""


@dataclass(frozen=True)
class ApiSocSample:
    value_percent: float
    measured_at: datetime
    retrieved_at: datetime


class KpNetSocClient:
    """SOC-only API client. Authentication is provided by Secret Manager."""

    def __init__(self, *, deadline_monotonic: float | None = None) -> None:
        self.deadline = deadline_monotonic if deadline_monotonic is not None else time.monotonic() + 60
        try:
            credentials = json.loads(os.environ.get("KP_SOC_API_CREDENTIALS_JSON", ""))
            authorization = credentials["authorization"]
            api_key = credentials["api_key"]
            self.user = {key: credentials["user"][key] for key in ("id", "password")}
            self.gateway = {key: credentials["gateway"][key] for key in ("id", "password")}
            values = [authorization, api_key, *self.user.values(), *self.gateway.values()]
            if not all(isinstance(value, str) and value and "\r" not in value and "\n" not in value for value in values):
                raise ValueError
            if not authorization.startswith("Basic "):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise SocApiError("KP_SOC_API_CREDENTIALS_JSON is missing or invalid") from None
        self.session = requests.Session()
        self.session.headers.update({"Authorization": authorization, "x-api-key": api_key})

    def close(self) -> None:
        self.session.close()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise SocApiError("SOC API deadline expired")
        try:
            response = self.session.post(
                "https://api.kp-net.com/gw/get-measurement-data/v2",
                json={"data": payload}, timeout=min(10.0, remaining / 2), allow_redirects=False,
            )
            if response.status_code != 200:
                raise SocApiError("SOC API HTTP request failed")
            if len(response.content) > 1_000_000:
                raise SocApiError("SOC API response exceeded size limit")
            result = response.json()
        except (requests.RequestException, ValueError):
            raise SocApiError("SOC API transport or JSON failure") from None
        if time.monotonic() >= self.deadline:
            raise SocApiError("SOC API deadline expired")
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or data.get("seqNo") != payload["seqNo"]:
            raise SocApiError("SOC API response correlation failed")
        if data.get("errPrt") != []:
            raise SocApiError("SOC API reported a device error")
        return data

    def read_soc(self) -> ApiSocSample:
        requested_at = datetime.now(JST).replace(microsecond=0)
        payload: dict[str, Any] = {
            "seqNo": f"{secrets.randbelow(100000):05d}",
            "reqDate": requested_at.isoformat(timespec="seconds").split("+")[0],
            "user": self.user, "gw": self.gateway,
            "measureFlg": "GW", "oldDataDt": None, "identifiId": None,
        }
        polls = 0
        while True:
            data = self._post(payload)
            polls += 1
            if data.get("endCd") == "00000":
                sample = parse_soc_measurement(data, requested_at=requested_at, retrieved_at=datetime.now(JST))
                if sample is None:
                    raise SocApiError("SOC API completed without a measurement")
                try:
                    print(json.dumps({"message": "kpnet-soc-api", "source": "kpnet_mobile_api",
                                      "soc_percent": sample.value_percent, "device_measured_at": sample.measured_at.isoformat(),
                                      "retrieved_at": sample.retrieved_at.isoformat(), "polls": polls,
                                      "measurement_age_seconds": (sample.retrieved_at - sample.measured_at).total_seconds()}), flush=True)
                except Exception:
                    pass
                return sample
            if data.get("endCd") != "20001":
                raise SocApiError("SOC API refresh was not accepted")
            identifier = data.get("identifiId")
            if not isinstance(identifier, str) or not identifier or data.get("mePrt") != []:
                raise SocApiError("SOC API pending response is invalid")
            payload["identifiId"] = identifier
            remaining = self.deadline - time.monotonic()
            if remaining <= 3:
                raise SocApiError("SOC API deadline expired while polling")
            time.sleep(3)


def parse_soc_measurement(
    data: dict[str, Any], *, requested_at: datetime, retrieved_at: datetime
) -> ApiSocSample | None:
    """Accept one unambiguous battery measurement produced after this request.

    The observed API uses offset-free Japanese local timestamps. Empty measurement
    arrays are a pending result, never a zero-percent battery reading.
    """
    measurements = data.get("mePrt")
    if not isinstance(measurements, list):
        raise SocApiError("SOC API measurement list missing")
    if not measurements:
        return None
    if len(measurements) != 1 or not isinstance(measurements[0], dict):
        raise SocApiError("SOC API measurement target is ambiguous")
    measurement = measurements[0]
    batteries = measurement.get("infoSto")
    if not isinstance(batteries, list) or len(batteries) != 1 or not isinstance(batteries[0], dict):
        raise SocApiError("SOC API battery target is missing or ambiguous")
    value = batteries[0].get("soc")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SocApiError("SOC API percentage is invalid")
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise SocApiError("SOC API percentage is invalid")
    timestamp = measurement.get("dtMe")
    if not isinstance(timestamp, str):
        raise SocApiError("SOC API measurement timestamp missing")
    try:
        measured_at = datetime.fromisoformat(timestamp)
    except ValueError:
        raise SocApiError("SOC API measurement timestamp invalid") from None
    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=JST)
    if requested_at.tzinfo is None or retrieved_at.tzinfo is None:
        raise SocApiError("SOC API request timestamps must be timezone-aware")
    if measured_at < requested_at.replace(microsecond=0):
        raise SocApiError("SOC API measurement predates refresh request")
    if measured_at > retrieved_at + timedelta(seconds=5):
        raise SocApiError("SOC API measurement timestamp is in the future")
    if retrieved_at - measured_at > timedelta(seconds=60):
        raise SocApiError("SOC API measurement is stale")
    return ApiSocSample(float(value), measured_at, retrieved_at)
