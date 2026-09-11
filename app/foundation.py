"""Source-agnostic curated M3U foundation loading and validation."""

from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.location_safety import (
    UnsafeLocationError, validate_foundation_location
)
from app.source_settings import load_source_settings
from app.version import __version__


MAX_M3U_BYTES = 20 * 1024 * 1024
ATTRIBUTE_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


class FoundationValidationError(Exception):
    """A configured-foundation error safe to return to the admin UI."""


def _read_foundation(location: str) -> bytes:
    try:
        location = validate_foundation_location(location)
    except UnsafeLocationError as error:
        raise FoundationValidationError(str(error)) from error
    parsed = urlsplit(location)
    if parsed.scheme in {"http", "https"}:
        request = Request(
            location,
            headers={
                "User-Agent": f"WonkEPG/{__version__}",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                status = getattr(response, "status", 200)
                if not 200 <= status < 300:
                    raise FoundationValidationError(
                        "Could not fetch configured M3U"
                    )
                contents = response.read(MAX_M3U_BYTES + 1)
        except FoundationValidationError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            raise FoundationValidationError("Could not fetch configured M3U")
    elif parsed.scheme:
        raise FoundationValidationError(
            "Configured M3U must be a local path or HTTP/HTTPS URL"
        )
    else:
        path = Path(location)
        try:
            if not path.is_file() or not os.access(path, os.R_OK):
                raise FoundationValidationError(
                    "Configured M3U file is not readable"
                )
            if path.stat().st_size > MAX_M3U_BYTES:
                raise FoundationValidationError(
                    "Configured M3U is too large to validate"
                )
            contents = path.read_bytes()
        except FoundationValidationError:
            raise
        except OSError:
            raise FoundationValidationError(
                "Configured M3U file is not readable"
            )
    if len(contents) > MAX_M3U_BYTES:
        raise FoundationValidationError(
            "Configured M3U is too large to validate"
        )
    return contents


def _attributes(line: str) -> dict[str, str]:
    return {
        key.casefold(): value.strip()
        for key, value in ATTRIBUTE_RE.findall(line)
    }


def inspect_m3u(contents: bytes) -> dict:
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FoundationValidationError("Configured M3U is not valid UTF-8")

    lines = [line.strip() for line in text.splitlines()]
    first = next((line for line in lines if line), "")
    if not first.startswith("#EXTM3U"):
        raise FoundationValidationError("Configured M3U is missing #EXTM3U")

    channels = []
    for line in lines:
        if not line.startswith("#EXTINF"):
            continue
        attrs = _attributes(line)
        display_name = line.split(",", 1)[1].strip() if "," in line else ""
        number = (
            attrs.get("tvg-chno")
            or attrs.get("channel-number")
            or attrs.get("chno")
            or ""
        )
        channel_id = (
            attrs.get("channelid")
            or attrs.get("tvg-id")
            or number
            or ""
        )
        name = attrs.get("tvg-name") or display_name or channel_id
        channels.append(
            {
                "channel_id": channel_id or None,
                "number": number or None,
                "name": name or None,
                "group": attrs.get("group-title") or None,
                "logo": attrs.get("tvg-logo") or None,
                "_metadata": {
                    "tvg_name": bool(attrs.get("tvg-name")),
                    "tvg_chno": bool(attrs.get("tvg-chno")),
                    "channel_id": bool(attrs.get("channelid")),
                    "group_title": bool(attrs.get("group-title")),
                    "tvg_logo": bool(attrs.get("tvg-logo")),
                },
            }
        )

    usable = [
        channel
        for channel in channels
        if channel["number"] and (channel["channel_id"] or channel["name"])
    ]
    if not channels:
        raise FoundationValidationError(
            "Configured M3U has no channel entries"
        )
    if not usable:
        raise FoundationValidationError(
            "Configured M3U has no usable numbered channels"
        )

    numbers = [channel["number"] for channel in channels if channel["number"]]
    counts = Counter(numbers)
    duplicates = sorted(
        (number for number, count in counts.items() if count > 1),
        key=lambda value: (
            int(value) if value.isascii() and value.isdecimal() else 10**12,
            value,
        ),
    )
    metadata = {
        name: sum(channel["_metadata"][name] for channel in channels)
        for name in (
            "tvg_name", "tvg_chno", "channel_id", "group_title", "tvg_logo"
        )
    }
    advisories = []
    labels = {
        "tvg_name": "tvg-name",
        "tvg_chno": "tvg-chno",
        "channel_id": "channelID",
        "group_title": "group-title",
        "tvg_logo": "tvg-logo",
    }
    for name, present in metadata.items():
        missing = len(channels) - present
        if missing:
            advisories.append(
                f"{missing} channel entries missing {labels[name]}"
            )
    if duplicates:
        advisories.append(
            f"{len(duplicates)} duplicate channel number"
            f"{'s' if len(duplicates) != 1 else ''}"
        )

    clean_channels = [
        {key: value for key, value in channel.items() if key != "_metadata"}
        for channel in usable
    ]
    return {
        "valid": True,
        "channel_count": len(channels),
        "usable_channel_count": len(clean_channels),
        "numbered_channel_count": len(numbers),
        "unique_channel_number_count": len(counts),
        "duplicate_channel_number_count": len(duplicates),
        "duplicate_channel_numbers": duplicates[:50],
        "missing_channel_number_count": len(channels) - len(numbers),
        "metadata_presence": metadata,
        "advisories": advisories,
        "channels": clean_channels,
    }


def load_foundation(location: str) -> dict:
    if not isinstance(location, str) or not location.strip():
        raise FoundationValidationError("Configured M3U is required")
    result = inspect_m3u(_read_foundation(location.strip()))
    return {
        "count": len(result["channels"]),
        "channels": result["channels"],
        "validation": {
            key: value for key, value in result.items() if key != "channels"
        },
    }


def validate_foundation(location: str) -> dict:
    """Validate one proposed path/URL without exposing or persisting it."""
    return load_foundation(location)["validation"]


def load_configured_foundation() -> dict:
    location = load_source_settings()["foundation"]["configured_m3u"]
    return load_foundation(location)
