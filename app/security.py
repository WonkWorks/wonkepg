"""Small native admin session and request-boundary helpers.

WonkEPG deliberately does not implement users, roles, password recovery, or an
identity database. One deployment-supplied password creates short-lived,
opaque, in-memory admin sessions. Restarting WonkEPG invalidates every session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
import threading


ADMIN_PASSWORD_ENV = "WONKEPG_ADMIN_PASSWORD"
SESSION_COOKIE = "wonkepg_admin_session"
CSRF_HEADER = "X-WonkEPG-CSRF"
DEFAULT_SESSION_MINUTES = 480
MIN_SESSION_MINUTES = 5
MAX_SESSION_MINUTES = 1440


@dataclass(frozen=True)
class AdminSession:
    token: str
    csrf_token: str
    expires_at: datetime


_LOCK = threading.Lock()
_SESSIONS: dict[str, AdminSession] = {}


def admin_password() -> str:
    return os.environ.get(ADMIN_PASSWORD_ENV, "")


def admin_auth_configured() -> bool:
    return bool(admin_password())


def cookie_secure() -> bool:
    return os.environ.get("WONKEPG_COOKIE_SECURE", "false").casefold() in {
        "1", "true", "yes", "on",
    }


def session_minutes() -> int:
    try:
        value = int(
            os.environ.get(
                "WONKEPG_SESSION_MINUTES", str(DEFAULT_SESSION_MINUTES)
            )
        )
    except ValueError:
        value = DEFAULT_SESSION_MINUTES
    return min(max(value, MIN_SESSION_MINUTES), MAX_SESSION_MINUTES)


def password_matches(candidate: object) -> bool:
    if not isinstance(candidate, str) or not admin_auth_configured():
        return False
    # Compare fixed-length digests so password length is not exposed through
    # compare timing. The password itself remains only in runtime environment.
    expected = hashlib.sha256(admin_password().encode("utf-8")).digest()
    supplied = hashlib.sha256(candidate.encode("utf-8")).digest()
    return hmac.compare_digest(expected, supplied)


def _discard_expired(now: datetime) -> None:
    expired = [
        token for token, session in _SESSIONS.items()
        if session.expires_at <= now
    ]
    for token in expired:
        _SESSIONS.pop(token, None)


def create_session(now: datetime | None = None) -> AdminSession:
    if not admin_auth_configured():
        raise RuntimeError("admin authentication is not configured")
    now = now or datetime.now(timezone.utc)
    session = AdminSession(
        token=secrets.token_urlsafe(32),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=now + timedelta(minutes=session_minutes()),
    )
    with _LOCK:
        _discard_expired(now)
        _SESSIONS[session.token] = session
    return session


def get_session(
    token: object, now: datetime | None = None
) -> AdminSession | None:
    if not isinstance(token, str) or not token:
        return None
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        _discard_expired(now)
        return _SESSIONS.get(token)


def destroy_session(token: object) -> None:
    if not isinstance(token, str):
        return
    with _LOCK:
        _SESSIONS.pop(token, None)


def clear_sessions() -> None:
    """Test/support hook; process restart also clears all sessions."""
    with _LOCK:
        _SESSIONS.clear()
