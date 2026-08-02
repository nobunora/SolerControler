"""Authentication, HTML, and URL helpers for the KP-NET client boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup


def clean_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "download.csv"


def extract_csrf(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one("meta[name='_csrf']")
    if meta and meta.get("content"):
        return str(meta["content"])
    hidden = soup.select_one("input[name='_csrf']")
    if hidden and hidden.get("value"):
        return str(hidden["value"])
    raise RuntimeError("_csrf をページから取得できませんでした")


def extract_alert_message(html: str) -> str:
    node = BeautifulSoup(html, "html.parser").select_one("div.alert.alert-danger")
    return node.get_text(" ", strip=True) if node else ""


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.string.strip() if soup.title and soup.title.string else ""


def parse_har_credentials(har_path: Path) -> tuple[str, str]:
    har = json.loads(har_path.read_text(encoding="utf-8"))
    for entry in har.get("log", {}).get("entries", []):
        request = entry.get("request", {})
        if request.get("method") != "POST" or not str(request.get("url", "")).endswith("/processLogin"):
            continue
        parsed = parse_qs(request.get("postData", {}).get("text", ""), keep_blank_values=True)
        username, password = parsed.get("loginid", [""])[0], parsed.get("loginpassword", [""])[0]
        if username and password:
            return username, password
    raise RuntimeError("HARから loginid / loginpassword を取得できませんでした")


def validate_base_url(*, base_url: str, enforce_https: bool, allowed_hosts: list[str]) -> None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        raise RuntimeError(f"KP_BASE_URL が不正です: {base_url}")
    if enforce_https and parsed.scheme.lower() != "https":
        raise RuntimeError("KP_BASE_URL は https URL を指定してください")
    allowed = {value.strip().lower() for value in allowed_hosts if value.strip()}
    if allowed and host not in allowed:
        raise RuntimeError(f"KP_BASE_URL のホストが許可リスト外です (host={host}, allowed={sorted(allowed)})")
