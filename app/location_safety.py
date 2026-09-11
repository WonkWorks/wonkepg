"""Validation boundaries for administrator-supplied URLs and paths."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit


class UnsafeLocationError(ValueError):
    """A location policy failure safe to display to an administrator."""


def _enabled(name: str) -> bool:
    return os.environ.get(name, "false").casefold() in {
        "1", "true", "yes", "on",
    }


def _unsafe_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return any((
        address.is_private,
        address.is_loopback,
        address.is_link_local,
        address.is_multicast,
        address.is_reserved,
        address.is_unspecified,
    ))


def allowed_source_hosts() -> set[str]:
    return {
        host.strip().casefold().rstrip(".")
        for host in os.environ.get(
            "WONKEPG_ALLOWED_SOURCE_HOSTS", ""
        ).split(",")
        if host.strip()
    }


def validate_http_location(value: str) -> str:
    """Allow HTTP(S), rejecting obvious internal-network SSRF by default."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UnsafeLocationError("location must be an HTTP or HTTPS URL")
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeLocationError("location must include a hostname")
    if (
        hostname.casefold().rstrip(".") in allowed_source_hosts()
        or _enabled("WONKEPG_ALLOW_PRIVATE_SOURCE_URLS")
    ):
        return value
    if hostname.casefold() in {"localhost", "localhost.localdomain"}:
        raise UnsafeLocationError("private or local network URLs are disabled")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        if _unsafe_address(hostname):
            raise UnsafeLocationError("private or local network URLs are disabled")
        return value
    try:
        answers = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        # The bounded fetch will return a sanitized resolution failure. Keeping
        # this path also permits deterministic mocked validation in tests.
        return value
    if any(_unsafe_address(answer[4][0]) for answer in answers):
        raise UnsafeLocationError("private or local network URLs are disabled")
    return value


def allowed_foundation_roots() -> tuple[Path, ...]:
    configured = os.environ.get(
        "WONKEPG_ALLOWED_FOUNDATION_ROOTS", "/foundation"
    )
    roots = []
    for raw in configured.split(os.pathsep):
        if raw.strip():
            roots.append(Path(raw.strip()).resolve())
    return tuple(roots)


def validate_foundation_location(value: str) -> str:
    """Allow HTTP(S) or a file contained by an explicit mounted root."""
    parsed = urlsplit(value)
    if parsed.scheme:
        return validate_http_location(value)
    candidate = Path(value).expanduser().resolve()
    if not any(candidate == root or candidate.is_relative_to(root)
               for root in allowed_foundation_roots()):
        raise UnsafeLocationError(
            "local foundation path is outside the configured allowed roots"
        )
    return str(candidate)
