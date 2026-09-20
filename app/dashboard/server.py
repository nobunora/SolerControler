from __future__ import annotations

import gzip
import hashlib
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
from urllib.parse import ParseResult, parse_qs, urlparse

from app.dashboard.data import load_dashboard_slice
from app.dashboard.models import DashboardSlice
from app.dashboard.snapshots import (
    BOOTSTRAP_KIND,
    HISTORY_KIND,
    SnapshotArtifact,
    artifact_from_payload,
    build_bootstrap_payload,
    build_dashboard_snapshot_payloads,
    load_precomputed_snapshot,
)


_PROJECT_ROOT = Path(__file__).parents[2]

_STATIC_ASSET_FILES = {
    "/static/dashboard.css": ("text/css; charset=utf-8", "dashboard.css"),
    "/static/dashboard_calculations.js": ("text/javascript; charset=utf-8", "dashboard_calculations.js"),
    "/static/dashboard_dates.js": ("text/javascript; charset=utf-8", "dashboard_dates.js"),
    "/static/dashboard_api.js": ("text/javascript; charset=utf-8", "dashboard_api.js"),
    "/static/dashboard_store.js": ("text/javascript; charset=utf-8", "dashboard_store.js"),
    "/static/dashboard.js": ("text/javascript; charset=utf-8", "dashboard.js"),
}
_STATIC_ASSETS: dict[str, tuple[str, bytes, bytes, str]] = {}
_static_version_digest = hashlib.sha256()
for _asset_path, (_content_type, _filename) in _STATIC_ASSET_FILES.items():
    _content = (_PROJECT_ROOT / "static" / _filename).read_bytes()
    _gzip_content = gzip.compress(_content, compresslevel=9, mtime=0)
    _etag = f'W/"{hashlib.sha256(_content).hexdigest()}"'
    _STATIC_ASSETS[_asset_path] = (_content_type, _content, _gzip_content, _etag)
    _static_version_digest.update(_asset_path.encode("utf-8"))
    _static_version_digest.update(b"\0")
    _static_version_digest.update(_content)
_STATIC_VERSION = _static_version_digest.hexdigest()[:16]


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


def _empty_dashboard_payload() -> dict[str, object]:
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


def _html(payload: dict[str, object], script_nonce: str) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    template = (_PROJECT_ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    return (\n        template.replace("__DASHBOARD_DATA_PLACEHOLDER__", payload_json)\n        .replace("__NONCE__", script_nonce)\n        .replace("__STATIC_VERSION__", _STATIC_VERSION)\n    )\n

def _static_asset(path: str) -> tuple[str, bytes, bytes, str] | None:
    return _STATIC_ASSETS.get(path)


def _static_version() -> str:
    return _STATIC_VERSION


class Handler(BaseHTTPRequestHandler):
    server_version = "SolarDashboard"
    sys_version = ""
    _new_session_cookie: str | None = None

    def _send_security_headers(self, script_nonce: str | None = None, *, cache_control: str = "no-store") -> None:
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
        self.send_header("Cache-Control", cache_control)

    def _accepts_gzip(self) -> bool:
        raw = self.headers.get("Accept-Encoding", "")
        return any(part.strip().split(";", 1)[0].lower() == "gzip" for part in raw.split(","))

    def _etag_matches(self, etag: str) -> bool:
        raw = self.headers.get("If-None-Match", "")
        if not raw:
            return False
        values = {part.strip() for part in raw.split(",")}
        return "*" in values or etag in values

    def _snapshot_fallback(self, kind: str) -> SnapshotArtifact:
        db_path = Path(_env("DATA_DB_PATH", "artifacts/solar_monitor.db"))
        if kind == BOOTSTRAP_KIND:
            value = load_dashboard_slice(
                db_path,
                end_date=None,
                window_days=31,
                include_static=True,
            )
            return artifact_from_payload(kind, build_bootstrap_payload(value))
        payloads = build_dashboard_snapshot_payloads(db_path)
        return artifact_from_payload(kind, payloads[kind])

    def _serve_snapshot(self, kind: str) -> None:
        artifact = load_precomputed_snapshot(kind)
        if artifact is None:
            artifact = self._snapshot_fallback(kind)

        cache_control = "private, max-age=0, must-revalidate"
        if self._etag_matches(artifact.etag):
            self.send_response(304)
            self.send_header("ETag", artifact.etag)
            self.send_header("Vary", "Accept-Encoding")
            self._maybe_send_auth_cookie()
            self._send_security_headers(cache_control=cache_control)
            self.end_headers()
            return

        use_gzip = self._accepts_gzip()
        body = artifact.gzip_bytes if use_gzip else artifact.raw
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", artifact.etag)
        self.send_header("Vary", "Accept-Encoding")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self._maybe_send_auth_cookie()
        self._send_security_headers(cache_control=cache_control)
        self.end_headers()
        self.wfile.write(body)

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
        if self._new_session_cookie is not None:
            self.send_header("Set-Cookie", self._new_session_cookie)

    def _is_authorized(self, parsed: ParseResult) -> bool:
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

    def _query_int(self, parsed: ParseResult, *, key: str, default: int, min_value: int, max_value: int) -> int:
        qs = parse_qs(parsed.query or "")
        raw = (qs.get(key) or [""])[0].strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return max(min_value, min(max_value, value))

    def _query_bool(self, parsed: ParseResult, *, key: str, default: bool) -> bool:
        qs = parse_qs(parsed.query or "")
        raw = (qs.get(key) or [""])[0].strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def _query_date(self, parsed: ParseResult, *, key: str) -> str | None:
        qs = parse_qs(parsed.query or "")
        raw = (qs.get(key) or [""])[0].strip()
        if not raw:
            return None
        try:
            _ = date.fromisoformat(raw)
        except ValueError:
            return None
        return raw

    # readable-code-audit: skip STRUCT-04 — route dispatch and HTTP response serialization share one request lifecycle and error boundary
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
            content_type, content, gzip_content, etag = static_asset
            version = (parse_qs(parsed.query or "").get("v") or [""])[0]
            cache_control = (
                "private, max-age=31536000, immutable"
                if version == _STATIC_VERSION
                else "private, max-age=0, must-revalidate"
            )
            if self._etag_matches(etag):
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Vary", "Accept-Encoding")
                self._send_security_headers(cache_control=cache_control)
                self.end_headers()
                return
            use_gzip = self._accepts_gzip()
            body = gzip_content if use_gzip else content
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
            self._send_security_headers(cache_control=cache_control)
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.query and "auth=" in parsed.query and (path == "/" or path == "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/")
            self._maybe_send_auth_cookie()
            self._send_security_headers()
            self.end_headers()
            return

        if path == "/" or path == "/index.html":
            # Return the HTML shell immediately. The browser loads the precomputed
            # bootstrap snapshot separately, so the first byte is no longer gated
            # on Firestore aggregation.
            payload = _empty_dashboard_payload()
            script_nonce = secrets.token_urlsafe(16)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._maybe_send_auth_cookie()
            self._send_security_headers(script_nonce=script_nonce)
            self.end_headers()
            self.wfile.write(_html(payload, script_nonce=script_nonce).encode("utf-8"))
            return
        if path == "/api/dashboard/bootstrap":
            try:
                self._serve_snapshot(BOOTSTRAP_KIND)
            except Exception:
                print("dashboard bootstrap snapshot error")
                print(traceback.format_exc())
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(b'{"error":"internal_error"}')
            return
        if path == "/api/dashboard/history":
            try:
                self._serve_snapshot(HISTORY_KIND)
            except Exception:
                print("dashboard history snapshot error")
                print(traceback.format_exc())
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(b'{"error":"internal_error"}')
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
                api_slice: DashboardSlice = load_dashboard_slice(
                    db_path,
                    end_date=end_date,
                    window_days=window_days,
                    include_static=include_static,
                )
                body = json.dumps(
                    {
                        **api_slice.data.__dict__,
                        "meta": api_slice.meta,
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

    def log_message(self, format: str, *args: object) -> None:
        _ = (format, args)


def main() -> int:
    host = _env("DASHBOARD_HOST", "127.0.0.1")
    port = int(_env("DASHBOARD_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Dashboard server running on http://{host}:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
