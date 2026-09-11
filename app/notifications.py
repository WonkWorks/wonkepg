"""Email notification settings and persistent failure/recovery state."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
import hashlib
import json
import os
import re
import smtplib
import threading
from typing import Callable

from app.scheduler import DEFAULT_SETTINGS, load_settings, update_settings


EVENT_PREFERENCES = {
    "source_refresh": "notify_source_refresh",
    "build": "notify_build",
    "stale_baseline": "notify_stale_mappings",
    "stale_epgshare": "notify_stale_mappings",
    "stale_epgtalk": "notify_stale_mappings",
    "stale_mappings": "notify_stale_mappings",
}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_NOTIFICATION_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_email(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 254:
        return False
    if "\r" in value or "\n" in value:
        return False
    parsed = parseaddr(value)[1]
    return parsed == value and EMAIL_PATTERN.fullmatch(value) is not None


def validate_error_handling(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("error handling settings must be an object")
    defaults = DEFAULT_SETTINGS["error_handling"]
    result = {}
    for field in (
        "enabled",
        "notify_source_refresh",
        "notify_build",
        "notify_stale_mappings",
        "notify_recovery",
    ):
        item = value.get(field, defaults[field])
        if not isinstance(item, bool):
            raise ValueError(f"{field} must be true or false")
        result[field] = item
    recipient = value.get("recipient", "")
    if not isinstance(recipient, str):
        raise ValueError("recipient must be a string")
    recipient = recipient.strip()
    if recipient and not valid_email(recipient):
        raise ValueError("recipient must be a valid email address")
    if result["enabled"] and not recipient:
        raise ValueError("recipient is required when notifications are enabled")
    result["recipient"] = recipient
    return result


def error_handling_status() -> dict:
    settings = load_settings()
    try:
        config = validate_error_handling(settings.get("error_handling"))
    except ValueError:
        config = deepcopy(DEFAULT_SETTINGS["error_handling"])
    return {**config, "smtp": smtp_status()}


def save_error_handling(value: object) -> dict:
    config = validate_error_handling(value)

    def mutate(settings: dict) -> None:
        existing = settings.get("error_handling")
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(config)
        settings["error_handling"] = merged

    update_settings(mutate)
    return error_handling_status()


def _smtp_config() -> dict:
    missing = [
        name
        for name in ("SMTP_HOST", "SMTP_FROM")
        if not os.environ.get(name, "").strip()
    ]
    port_text = os.environ.get("SMTP_PORT", "25").strip()
    try:
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        missing.append("SMTP_PORT")
        port = 25
    return {
        "configured": not missing,
        "missing": sorted(set(missing)),
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": port,
        "sender": os.environ.get("SMTP_FROM", "").strip(),
        "username": os.environ.get("SMTP_USERNAME", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "starttls": os.environ.get("SMTP_STARTTLS", "").lower()
        in {"1", "true", "yes", "on"},
        "ssl": os.environ.get("SMTP_USE_SSL", "").lower()
        in {"1", "true", "yes", "on"},
    }


def smtp_status() -> dict:
    config = _smtp_config()
    return {
        "configured": config["configured"],
        "missing": config["missing"],
    }


def _send_message(recipient: str, subject: str, body: str) -> None:
    config = _smtp_config()
    if not config["configured"]:
        missing = ", ".join(config["missing"])
        raise ValueError(f"SMTP is not configured; missing: {missing}")
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    smtp_class = smtplib.SMTP_SSL if config["ssl"] else smtplib.SMTP
    with smtp_class(config["host"], config["port"], timeout=15) as client:
        if config["starttls"] and not config["ssl"]:
            client.starttls()
        if config["username"]:
            client.login(config["username"], config["password"])
        client.send_message(message)


def send_test_email() -> dict:
    settings = error_handling_status()
    recipient = settings["recipient"]
    if not recipient:
        raise ValueError("save a valid recipient before sending a test")
    _send_message(
        recipient,
        "WonkEPG test notification",
        (
            "Service: WonkEPG\n"
            "Event: test\n"
            f"Time: {_utc_now()}\n"
            "Status: Email notification delivery is working.\n"
        ),
    )
    return {"sent": True, "recipient": recipient}


def _safe_detail(value: object, limit: int = 200) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value))
    text = re.sub(
        r"\b(?:https?|ftp)://\S+",
        "[redacted URL]",
        text,
        flags=re.IGNORECASE,
    )
    return text[:limit]


def _fingerprint(details: dict) -> str:
    encoded = json.dumps(details, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _process_event(
    event_type: str,
    failed: bool,
    details: dict,
    sender: Callable[[str, str, str], None] = _send_message,
) -> dict:
    """Persist event state and notify only on a new failure or recovery."""
    if event_type not in EVENT_PREFERENCES:
        raise ValueError("unsupported notification event")
    settings = load_settings()
    try:
        config = validate_error_handling(settings.get("error_handling"))
    except ValueError:
        config = deepcopy(DEFAULT_SETTINGS["error_handling"])
    states = settings.get("notification_state")
    if not isinstance(states, dict):
        states = {}
    previous = states.get(event_type)
    if not isinstance(previous, dict):
        previous = {}

    fingerprint = _fingerprint(details)
    was_active = bool(previous.get("active"))
    prior_fingerprint = previous.get("fingerprint")
    last_notified = previous.get("last_notified_fingerprint")
    notification_kind = None
    should_send = False
    if failed:
        notification_kind = "failure"
        should_send = (
            config["enabled"]
            and config[EVENT_PREFERENCES[event_type]]
            and fingerprint != last_notified
        )
    elif was_active:
        notification_kind = "recovery"
        should_send = (
            config["enabled"]
            and config["notify_recovery"]
            and bool(previous.get("last_notified_fingerprint"))
        )

    sent = False
    delivery_error = None
    if should_send:
        status = _safe_detail(
            details.get("status", "failed" if failed else "recovered")
        )
        subject = f"WonkEPG {event_type.replace('_', ' ')} {notification_kind}"
        body = (
            "Service: WonkEPG\n"
            f"Event: {event_type}\n"
            f"State: {notification_kind}\n"
            f"Time: {_utc_now()}\n"
            f"Status: {status}\n"
        )
        affected = details.get("affected")
        if affected:
            body += f"Affected: {', '.join(str(item) for item in affected)}\n"
        try:
            sender(config["recipient"], subject, body)
            sent = True
        except Exception as error:
            delivery_error = f"delivery failed ({type(error).__name__})"

    state = {
        "active": failed,
        "fingerprint": fingerprint if failed else None,
        "changed_at": (
            previous.get("changed_at")
            if (
                (failed and was_active and fingerprint == prior_fingerprint)
                or (not failed and not was_active)
            )
            else _utc_now()
        ),
        "last_notified_fingerprint": (
            fingerprint
            if failed and sent
            else (
                previous.get("last_notified_fingerprint")
                if failed
                else None
            )
        ),
        "last_delivery_error": delivery_error,
    }

    def mutate(current: dict) -> None:
        current_states = current.get("notification_state")
        if not isinstance(current_states, dict):
            current_states = {}
            current["notification_state"] = current_states
        current_states[event_type] = state

    update_settings(mutate)
    return {
        "event": event_type,
        "transition": notification_kind,
        "sent": sent,
        "delivery_error": delivery_error,
    }


def process_event(
    event_type: str,
    failed: bool,
    details: dict,
    sender: Callable[[str, str, str], None] = _send_message,
) -> dict:
    """Serialize state transitions so concurrent runs cannot double-notify."""
    with _NOTIFICATION_LOCK:
        return _process_event(event_type, failed, details, sender)


def process_refresh_result(result: dict) -> dict:
    failed_sources = [
        str(item.get("source", "unknown"))
        for item in result.get("sources") or []
        if not item.get("success")
    ]
    return process_event(
        "source_refresh",
        bool(failed_sources),
        {
            "status": (
                "one or more source refreshes failed"
                if failed_sources
                else "all sources refreshed successfully"
            ),
            "affected": failed_sources,
        },
    )


def _stale_affected(result: dict, source: str) -> list[str]:
    affected = []
    for item in result.get(f"stale_{source}_mappings") or []:
        identity = item.get("channel_id")
        if source == "baseline":
            identity = (
                f"{item.get('source')}/{identity} ({item.get('reason')})"
            )
        affected.append(f"channel {item.get('number')}: {identity}")
    return affected


def process_stale_result(
    result: dict,
    sender: Callable[[str, str, str], None] = _send_message,
) -> dict:
    """Process independently recoverable baseline and enrichment states."""
    events = {}
    labels = {
        "baseline": "baseline",
        "epgshare": "Enrichment 1",
        "epgtalk": "Enrichment 2",
    }
    for source, label in labels.items():
        affected = _stale_affected(result, source)
        event_type = f"stale_{source}"
        events[event_type] = process_event(
            event_type,
            bool(affected),
            {
                "status": (
                    f"saved {label} mappings are unavailable"
                    if affected
                    else f"saved {label} mappings are available"
                ),
                "affected": affected,
            },
            sender=sender,
        )
    return events


def process_build_result(
    result: dict,
    sender: Callable[[str, str, str], None] = _send_message,
) -> dict:
    """Process build plus independently recoverable stale-source states."""
    validation = result.get("parse_validation") or {}
    failed = not bool(result.get("output_written") and validation.get("valid"))
    events = {
        "build": process_event(
            "build",
            failed,
            {
                "status": (
                    "XMLTV build failed validation"
                    if failed
                    else "XMLTV build completed successfully"
                ),
                "affected": [],
            },
            sender=sender,
        )
    }
    if failed:
        return events

    events.update(process_stale_result(result, sender=sender))
    return events


def process_scheduled_result(result: dict) -> None:
    refresh = result.get("refresh")
    if isinstance(refresh, dict):
        process_refresh_result(refresh)
    if result.get("stage") in {"build", "complete"}:
        build = result.get("build")
        if isinstance(build, dict):
            normalized = {
                **build,
                "output_written": bool(
                    result.get("success") or build.get("output_written")
                ),
            }
            process_build_result(normalized)
        else:
            process_event(
                "build",
                True,
                {"status": "XMLTV build failed", "affected": []},
            )


def report_stale_mappings(affected: list[str]) -> dict:
    """Extension point for the existing/future drift detector."""
    return process_event(
        "stale_mappings",
        bool(affected),
        {
            "status": (
                "saved enrichment mappings are unavailable"
                if affected
                else "saved enrichment mappings are available"
            ),
            "affected": [str(item) for item in affected],
        },
    )
