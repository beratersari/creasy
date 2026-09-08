"""Dashboard login: username/password from env, httpOnly session cookie."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from creasy.config import Config

SESSION_COOKIE = "creasy_session"
SESSION_MAX_AGE = 12 * 60 * 60
_SESSION_VERSION = "v1"


def dashboard_auth_required(cfg: Config) -> bool:
    return bool(cfg.dashboard_token or cfg.dashboard_password)


def has_username(cfg: Config) -> bool:
    return bool(cfg.dashboard_user)


def _digest_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(left.encode("utf-8")).digest(),
        hashlib.sha256(right.encode("utf-8")).digest(),
    )


def _session_secret(cfg: Config) -> bytes:
    raw = cfg.dashboard_password or cfg.dashboard_token or ""
    return hashlib.sha256(raw.encode("utf-8")).digest()


def credentials_ok(cfg: Config, username: str, password: str) -> bool:
    if not dashboard_auth_required(cfg):
        return True
    if not password:
        return False
    if cfg.dashboard_user and not _digest_eq(username, cfg.dashboard_user):
        return False
    if cfg.dashboard_password:
        return _digest_eq(password, cfg.dashboard_password)
    if cfg.dashboard_token:
        return _digest_eq(password, cfg.dashboard_token)
    return False


def make_session(cfg: Config, now: int | None = None) -> str:
    ts = str(int(now if now is not None else time.time()))
    msg = f"{_SESSION_VERSION}|{ts}".encode("utf-8")
    sig = hmac.new(_session_secret(cfg), msg, hashlib.sha256).hexdigest()
    return f"{_SESSION_VERSION}.{ts}.{sig}"


def session_ok(cfg: Config, value: str, *, now: int | None = None) -> bool:
    if not value or not dashboard_auth_required(cfg):
        return False
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != _SESSION_VERSION:
        return False
    version, ts, sig = parts
    try:
        issued = int(ts)
    except ValueError:
        return False
    current = int(now if now is not None else time.time())
    if current < issued or current - issued > SESSION_MAX_AGE:
        return False
    msg = f"{version}|{ts}".encode("utf-8")
    expected = hmac.new(_session_secret(cfg), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def provided_api_token(headers: Any) -> str:
    header = (headers.get("x-creasy-token") or headers.get("X-Creasy-Token") or "").strip()
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return (header or bearer).strip()


def api_token_ok(cfg: Config, token: str) -> bool:
    if not cfg.dashboard_token or not token:
        return False
    return _digest_eq(token, cfg.dashboard_token)


def request_authenticated(cfg: Config, *, cookie: str, headers: Any) -> bool:
    if not dashboard_auth_required(cfg):
        return True
    if session_ok(cfg, cookie):
        return True
    return api_token_ok(cfg, provided_api_token(headers))
