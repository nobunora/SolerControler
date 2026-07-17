from __future__ import annotations

import json
import os
import base64
import hmac
import secrets
import time
import traceback
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.dashboard_data import DashboardSlice, load_dashboard_slice


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _auth_enabled() -> bool:
    return bool(os.getenv("DASHBOARD_BASIC_USER", "").strip()) and bool(os.getenv("DASHBOARD_BASIC_PASSWORD", "").strip())


def _decode_auth_token(token: str) -> tuple[str, str] | None:
    raw = token.strip()
    if not raw:
        return None
    pad = "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("utf-8")).decode("utf-8")
    except Exception:
        return None
    if ":" not in decoded:
        return None
    user, passwd = decoded.split(":", 1)
    return user, passwd


def _session_secret() -> bytes:
    explicit = os.getenv("DASHBOARD_SESSION_SECRET", "").strip()
    if explicit:
        return explicit.encode("utf-8")
    # Fallback keeps behavior deterministic even when secret is not explicitly set.
    return f"{os.getenv('DASHBOARD_BASIC_USER', '')}:{os.getenv('DASHBOARD_BASIC_PASSWORD', '')}".encode("utf-8")


def _sign_session(expire_unix: int) -> str:
    msg = str(expire_unix).encode("utf-8")
    sig = hmac.new(_session_secret(), msg, "sha256").digest()
    return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")


def _verify_session(token: str) -> bool:
    if "." not in token:
        return False
    exp_str, got_sig = token.split(".", 1)
    if not exp_str.isdigit():
        return False
    exp = int(exp_str)
    if exp <= int(time.time()):
        return False
    expected = _sign_session(exp)
    return hmac.compare_digest(got_sig, expected)


def _empty_dashboard_payload() -> dict:
    return {
        "pv_daily": [],
        "forecast_hourly": [],
        "energy_daily": [],
        "cost_daily": [],
        "cost_monthly": [],
        "battery_daily": [],
        "battery_flow_daily": [],
        "model_parameters": [],
        "latest_schedule": {},
        "dashboard_warnings": [],
        "pv_forecast_diagnostics": {},
        "daily_review": {},
        "daily_reviews": [],
        "meta": {
            "window_days": 31,
            "oldest_loaded_date": None,
            "newest_loaded_date": None,
            "global_oldest_date": None,
            "global_newest_date": None,
            "has_more_before": False,
        },
    }


def _html(payload: dict, script_nonce: str) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    template = (Path(__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
    return template.replace("__DASHBOARD_DATA_PLACEHOLDER__", payload_json).replace("__NONCE__", script_nonce)


def _static_asset(path: str) -> tuple[str, bytes] | None:
    assets = {
        "/static/dashboard.css": ("text/css; charset=utf-8", "dashboard.css"),
        "/static/dashboard_calculations.js": ("text/javascript; charset=utf-8", "dashboard_calculations.js"),
        "/static/dashboard_dates.js": ("text/javascript; charset=utf-8", "dashboard_dates.js"),
        "/static/dashboard_api.js": ("text/javascript; charset=utf-8", "dashboard_api.js"),
        "/static/dashboard_store.js": ("text/javascript; charset=utf-8", "dashboard_store.js"),
        "/static/dashboard.js": ("text/javascript; charset=utf-8", "dashboard.js"),
    }
    asset = assets.get(path)
    if asset is None:
        return None
    content_type, filename = asset
    content = (Path(__file__).parent / "static" / filename).read_bytes()
    return content_type, content


class Handler(BaseHTTPRequestHandler):
    server_version = "SolarDashboard"
    sys_version = ""

    def _send_security_headers(self, script_nonce: str | None = None) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        script_src = "script-src 'self' https://cdn.jsdelivr.net"
        if script_nonce:
            script_src = f"{script_src} 'nonce-{script_nonce}'"
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'self'; {script_src}; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none';",
        )
        self.send_header("Cache-Control", "no-store")

    def _cookie_secure_flag(self) -> bool:
        explicit = os.getenv("DASHBOARD_COOKIE_SECURE", "").strip().lower()
        if explicit in {"1", "true", "yes", "on"}:
            return True
        if explicit in {"0", "false", "no", "off"}:
            return False
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").lower()
        host = self.headers.get("Host", "").lower()
        return forwarded_proto == "https" or ("localhost" not in host and "127.0.0.1" not in host)

    def _extract_cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        parts = [p.strip() for p in raw.split(";")]
        key = f"{name}="
        for part in parts:
            if part.startswith(key):
                return part[len(key) :]
        return None

    def _build_session_cookie(self) -> str:
        ttl = int(_env("DASHBOARD_SESSION_TTL_SECONDS", "31536000"))
        exp = int(time.time()) + max(60, ttl)
        token = f"{exp}.{_sign_session(exp)}"
        bits = [
            f"sdash={token}",
            "Path=/",
            f"Max-Age={max(60, ttl)}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if self._cookie_secure_flag():
            bits.append("Secure")
        return "; ".join(bits)

    def _maybe_send_auth_cookie(self) -> None:
        if getattr(self, "_new_session_cookie", None):
            self.send_header("Set-Cookie", self._new_session_cookie)

    def _is_authorized(self, parsed) -> bool:
        if not _auth_enabled():
            return True

        session = self._extract_cookie("sdash")
        if session and _verify_session(session):
            return True

        qs = parse_qs(parsed.query or "")
        token_list = qs.get("auth", [])
        if token_list:
            creds = _decode_auth_token(token_list[0])
            if creds:
                expected_user = os.getenv("DASHBOARD_BASIC_USER", "")
                expected_passwd = os.getenv("DASHBOARD_BASIC_PASSWORD", "")
                user, passwd = creds
                if hmac.compare_digest(user, expected_user) and hmac.compare_digest(passwd, expected_passwd):
                    self._new_session_cookie = self._build_session_cookie()
                    return True

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        encoded = auth[6:].strip()
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except Exception:
            return False
        if ":" not in decoded:
            return False
        user, passwd = decoded.split(":", 1)
        expected_user = os.getenv("DASHBOARD_BASIC_USER", "")
        expected_passwd = os.getenv("DASHBOARD_BASIC_PASSWORD", "")
        ok = hmac.compare_digest(user, expected_user) and hmac.compare_digest(passwd, expected_passwd)
        if ok:
            self._new_session_cookie = self._build_session_cookie()
        return ok

    def _query_int(self, parsed, *, key: str, default: int, min_value: int, max_value: int) -> int:
        qs = parse_qs(parsed.query or "")
        raw = (qs.get(key) or [""])[0].strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return max(min_value, min(max_value, value))

    def _query_bool(self, parsed, *, key: str, default: bool) -> bool:
        qs = parse_qs(parsed.query or "")
        raw = (qs.get(key) or [""])[0].strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def _query_date(self, parsed, *, key: str) -> str | None:
        qs = parse_qs(parsed.query or "")
        raw = (qs.get(key) or [""])[0].strip()
        if not raw:
            return None
        try:
            _ = date.fromisoformat(raw)
        except ValueError:
            return None
        return raw

    def do_GET(self) -> None:  # noqa: N802
        self._new_session_cookie = None
        parsed = urlparse(self.path)
        if not self._is_authorized(parsed):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="solar-dashboard"')
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}')
            return

        path = parsed.path
        static_asset = _static_asset(path)
        if static_asset is not None:
            content_type, content = static_asset
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(content)
            return
        if parsed.query and "auth=" in parsed.query and (path == "/" or path == "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/")
            self._maybe_send_auth_cookie()
            self._send_security_headers()
            self.end_headers()
            return

        if path == "/" or path == "/index.html":
            try:
                db_path = Path(_env("DATA_DB_PATH", "artifacts/solar_monitor.db"))
                sliced = load_dashboard_slice(
                    db_path,
                    end_date=None,
                    window_days=31,
                    include_static=True,
                )
                payload = {
                    **sliced.data.__dict__,
                    "meta": sliced.meta,
                }
            except Exception:
                print("dashboard root render error")
                print(traceback.format_exc())
                payload = _empty_dashboard_payload()
            script_nonce = secrets.token_urlsafe(16)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._maybe_send_auth_cookie()
            self._send_security_headers(script_nonce=script_nonce)
            self.end_headers()
            self.wfile.write(_html(payload, script_nonce=script_nonce).encode("utf-8"))
            return
        if path == "/api/dashboard":
            try:
                db_path = Path(_env("DATA_DB_PATH", "artifacts/solar_monitor.db"))
                window_days = self._query_int(
                    parsed,
                    key="window_days",
                    default=31,
                    min_value=1,
                    max_value=365,
                )
                include_static = self._query_bool(parsed, key="include_static", default=True)
                end_date = self._query_date(parsed, key="end_date")
                sliced: DashboardSlice = load_dashboard_slice(
                    db_path,
                    end_date=end_date,
                    window_days=window_days,
                    include_static=include_static,
                )
                body = json.dumps(
                    {
                        **sliced.data.__dict__,
                        "meta": sliced.meta,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._maybe_send_auth_cookie()
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                print("dashboard api error")
                print(traceback.format_exc())
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(b'{"error":"internal_error"}')
            return

        self.send_response(404)
        self._send_security_headers()
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        _ = (fmt, args)


def main() -> int:
    host = _env("DASHBOARD_HOST", "127.0.0.1")
    port = int(_env("DASHBOARD_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Dashboard server running on http://{host}:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
