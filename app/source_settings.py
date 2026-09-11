"""Credential-safe runtime XMLTV source configuration and validation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from app.location_safety import (
    UnsafeLocationError,
    validate_foundation_location,
    validate_http_location,
)
from app.version import __version__


CONFIG_DIR = Path("/app/config")
SOURCES_PATH = CONFIG_DIR / "sources.json"
# Streaming validation safety ceilings, not in-memory allocation sizes.
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_XML_BYTES = 2 * 1024 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
VALIDATION_SPOOL_MEMORY_BYTES = 2 * 1024 * 1024
VALIDATION_REQUEST_TIMEOUT_SECONDS = 30
VALIDATION_TOTAL_TIMEOUT_SECONDS = 180
DEFAULT_FOUNDATION_M3U = "/foundation/channels.m3u"
DEFAULT_ENRICHMENT_URL = ""
DEFAULT_ENRICHMENT_SECONDARY_URL = ""
_LOCK = threading.Lock()
SOURCE_SETTINGS_SCHEMA_VERSION = 2
DEFAULT_BASELINE_SOURCE_ID = "baseline-default"
SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


def default_source_settings() -> dict:
    return {
        "schema_version": SOURCE_SETTINGS_SCHEMA_VERSION,
        "foundation": {
            "configured_m3u": DEFAULT_FOUNDATION_M3U,
        },
        "baseline": {
            "default_source": DEFAULT_BASELINE_SOURCE_ID,
        },
        "schedule_sources": {
            DEFAULT_BASELINE_SOURCE_ID: {
                # HIVE_XMLTV_URL is a 0.7.x migration fallback only.
                "url": os.environ.get(
                    "WONKEPG_DEFAULT_XMLTV_URL",
                    os.environ.get("HIVE_XMLTV_URL", ""),
                ).strip(),
                "provider_name": "Default Schedule",
            },
        },
        "enrichment_1": {
            "url": DEFAULT_ENRICHMENT_URL,
            "provider_name": "Enrichment 1",
        },
        "enrichment_2": {
            "primary_url": DEFAULT_ENRICHMENT_URL,
            "secondary_url": DEFAULT_ENRICHMENT_SECONDARY_URL,
            "provider_name": "Enrichment 2",
        },
    }


def _validate_url(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if not value:
        return ""
    try:
        return validate_http_location(value)
    except UnsafeLocationError as error:
        raise ValueError(f"{field}: {error}") from error


def _provider_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("provider name must be text")
    value = value.strip()
    if len(value) > 100:
        raise ValueError("provider name must be at most 100 characters")
    return value


def _source_id(value: object) -> str:
    if not isinstance(value, str) or not SOURCE_ID_RE.fullmatch(value):
        raise ValueError(
            "schedule source ID must use lowercase letters, numbers, and hyphens"
        )
    if len(value) > 64:
        raise ValueError("schedule source ID must be at most 64 characters")
    return value


def schedule_cache_filename(source_id: object) -> str:
    """Return a path-safe cache name derived only from an immutable ID."""
    return f"schedule-{_source_id(source_id)}.xml"


def _validated_schedule_sources(value: object) -> dict:
    if not isinstance(value, dict) or not value:
        raise ValueError("at least one schedule source is required")
    if len(value) > 32:
        raise ValueError("at most 32 schedule sources are supported")
    validated = {}
    for raw_source_id, raw_source in value.items():
        source_id = _source_id(raw_source_id)
        if not isinstance(raw_source, dict):
            raise ValueError(f"schedule source {source_id} must be an object")
        validated[source_id] = {
            "url": _validate_url(
                raw_source.get("url", ""),
                f"schedule source {source_id} URL",
            ),
            "provider_name": _provider_name(
                raw_source.get("provider_name", "")
            ),
        }
    return validated


def _foundation_location(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Configured M3U must be text")
    value = value.strip()
    if not value:
        raise ValueError("Configured M3U is required")
    try:
        return validate_foundation_location(value)
    except UnsafeLocationError as error:
        raise ValueError(f"Configured M3U: {error}") from error


def validate_source_settings(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("source settings must be an object")
    baseline = value.get("baseline")
    enrich1 = value.get("enrichment_1")
    enrich2 = value.get("enrichment_2")
    foundation = value.get("foundation", {})
    if not all(isinstance(item, dict) for item in (baseline, enrich1, enrich2)):
        raise ValueError("all three source roles are required")
    schedule_sources = value.get("schedule_sources")
    if schedule_sources is None:
        schedule_sources = {
            DEFAULT_BASELINE_SOURCE_ID: {
                "url": baseline.get("url", ""),
                "provider_name": baseline.get("provider_name", ""),
            },
        }
        default_source = DEFAULT_BASELINE_SOURCE_ID
    else:
        default_source = _source_id(
            baseline.get("default_source", DEFAULT_BASELINE_SOURCE_ID)
        )
    validated_sources = _validated_schedule_sources(schedule_sources)
    if default_source not in validated_sources:
        raise ValueError("default schedule source is not configured")
    return {
        "schema_version": SOURCE_SETTINGS_SCHEMA_VERSION,
        "foundation": {
            "configured_m3u": _foundation_location(
                foundation.get("configured_m3u", DEFAULT_FOUNDATION_M3U)
            ),
        },
        "baseline": {
            "default_source": default_source,
        },
        "schedule_sources": validated_sources,
        "enrichment_1": {
            "url": _validate_url(
                enrich1.get("url", ""), "Enrichment 1 URL"
            ),
            "provider_name": _provider_name(enrich1.get("provider_name", "")),
        },
        "enrichment_2": {
            "primary_url": _validate_url(
                enrich2.get("primary_url", ""), "Enrichment 2 primary URL"
            ),
            "secondary_url": _validate_url(
                enrich2.get("secondary_url", ""),
                "Enrichment 2 secondary URL",
            ),
            "provider_name": _provider_name(enrich2.get("provider_name", "")),
        },
    }


def _atomic_write(value: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".sources.", suffix=".tmp", dir=CONFIG_DIR
    )
    os.fchmod(descriptor, 0o600)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, SOURCES_PATH)
        os.chmod(SOURCES_PATH, 0o600)
        directory = os.open(CONFIG_DIR, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def ensure_source_settings() -> dict:
    with _LOCK:
        if SOURCES_PATH.is_file():
            try:
                stored = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
                validated = validate_source_settings(stored)
                if stored != validated:
                    if stored.get("schema_version") != SOURCE_SETTINGS_SCHEMA_VERSION:
                        backup = SOURCES_PATH.with_name(
                            "sources.json.pre-0.6.0.bak"
                        )
                        if not backup.exists():
                            shutil.copy2(SOURCES_PATH, backup)
                    _atomic_write(validated)
                return validated
            except (OSError, json.JSONDecodeError, ValueError):
                raise ValueError("runtime source configuration is invalid")
        settings = default_source_settings()
        _atomic_write(settings)
        return deepcopy(settings)


def load_source_settings() -> dict:
    return ensure_source_settings()


def save_source_settings(value: object) -> dict:
    validated = validate_source_settings(value)
    with _LOCK:
        _atomic_write(validated)
    return deepcopy(validated)


_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:token|key|secret|pass|user|auth|credential)", re.IGNORECASE
)


def url_contains_credentials(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.username is not None or parsed.password is not None:
        return True
    from urllib.parse import parse_qsl
    return any(
        field_value and _SENSITIVE_QUERY_KEY.search(field_name)
        for field_name, field_value in parse_qsl(
            parsed.query, keep_blank_values=True
        )
    )


def _source_url_fields(settings: dict):
    yield ("foundation.configured_m3u", settings["foundation"], "configured_m3u")
    for source_id, source in settings["schedule_sources"].items():
        yield (f"schedule_sources.{source_id}.url", source, "url")
    yield ("enrichment_1.url", settings["enrichment_1"], "url")
    yield (
        "enrichment_2.primary_url", settings["enrichment_2"], "primary_url"
    )
    yield (
        "enrichment_2.secondary_url",
        settings["enrichment_2"],
        "secondary_url",
    )


def public_source_settings(settings: dict | None = None) -> dict:
    """Return admin-editable settings without credential-bearing URLs."""
    public = deepcopy(settings or load_source_settings())
    redacted = []
    for field, container, key in _source_url_fields(public):
        if url_contains_credentials(container.get(key)):
            container[key] = ""
            redacted.append(field)
    public["redacted_urls"] = redacted
    return public


def preserve_redacted_source_urls(proposed: object, current: dict) -> object:
    """Treat blank masked URL fields as unchanged during an admin save."""
    if not isinstance(proposed, dict):
        return proposed
    merged = deepcopy(proposed)
    try:
        current_fields = {
            field: (container, key)
            for field, container, key in _source_url_fields(current)
        }
        proposed_fields = {
            field: (container, key)
            for field, container, key in _source_url_fields(merged)
        }
    except (KeyError, TypeError):
        return merged
    for field, (current_container, current_key) in current_fields.items():
        proposed_container, proposed_key = proposed_fields[field]
        if (
            url_contains_credentials(current_container.get(current_key))
            and not proposed_container.get(proposed_key)
        ):
            proposed_container[proposed_key] = current_container[current_key]
    return merged


class SourceValidationError(Exception):
    pass


def _check_validation_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise SourceValidationError("Source validation timed out")


def _stream_download(url: str, destination, deadline: float) -> None:
    try:
        validated_url = _validate_url(url, "source URL")
    except ValueError:
        raise SourceValidationError("Could not fetch source")
    if not validated_url:
        raise SourceValidationError("Source URL is required")
    request = Request(
        validated_url,
        headers={"User-Agent": f"WonkEPG/{__version__}", "Accept-Encoding": "identity"},
    )
    downloaded = 0
    try:
        with urlopen(
            request, timeout=VALIDATION_REQUEST_TIMEOUT_SECONDS
        ) as response:
            status = getattr(response, "status", 200)
            if not 200 <= status < 300:
                raise SourceValidationError("Could not fetch source")
            while True:
                _check_validation_deadline(deadline)
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                _check_validation_deadline(deadline)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise SourceValidationError(
                        "Source exceeds the validation download safety limit"
                    )
                destination.write(chunk)
    except SourceValidationError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise SourceValidationError("Could not fetch source")


class _LimitedReader:
    """File-like read guard for bounded streaming decompression/parsing."""

    def __init__(self, source, limit: int, deadline: float):
        self.source = source
        self.limit = limit
        self.deadline = deadline
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        _check_validation_deadline(self.deadline)
        if size < 0:
            size = DOWNLOAD_CHUNK_BYTES
        chunk = self.source.read(size)
        _check_validation_deadline(self.deadline)
        self.total += len(chunk)
        if self.total > self.limit:
            raise SourceValidationError(
                "XMLTV exceeds the decompressed validation safety limit"
            )
        return chunk


def _xmltv_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:20], "%Y%m%d%H%M%S %z")
    except (ValueError, TypeError):
        return None


def _hostname(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return urlsplit(value).hostname
    except ValueError:
        return None


def _inspect_xmltv_stream(source, configured_url: str) -> dict:
    root_tag = None
    source_name = None
    source_info_host = None
    channels = 0
    programmes = 0
    first_start = None
    last_start = None
    last_stop = None

    for event, element in ET.iterparse(source, events=("start", "end")):
        tag = element.tag.rsplit("}", 1)[-1]
        if root_tag is None and event == "start":
            root_tag = tag
            if root_tag != "tv":
                raise SourceValidationError("Root element is not <tv>")
            source_name = (element.get("source-info-name") or "").strip() or None
            source_info_host = _hostname(element.get("source-info-url"))
        if event == "end" and tag == "channel":
            channels += 1
        elif event == "end" and tag == "programme":
            programmes += 1
            start = _xmltv_datetime(element.get("start"))
            stop = _xmltv_datetime(element.get("stop"))
            if start is not None:
                first_start = min(first_start, start) if first_start else start
                last_start = max(last_start, start) if last_start else start
            if stop is not None:
                last_stop = max(last_stop, stop) if last_stop else stop
        if event == "end":
            element.clear()

    if root_tag != "tv":
        raise SourceValidationError("Root element is not <tv>")
    if not channels and not programmes:
        raise SourceValidationError("No XMLTV channels/programmes found")

    configured_host = _hostname(configured_url)
    suggested = source_name or source_info_host or configured_host or "XMLTV Source"
    last_programme_time = max(
        (value for value in (last_start, last_stop) if value is not None),
        default=None,
    )
    horizon_days = None
    if first_start and last_programme_time:
        horizon_days = round(
            (last_programme_time - first_start).total_seconds() / 86400, 1
        )
    return {
        "valid": True,
        "channel_count": channels,
        "programme_count": programmes,
        "horizon_days": horizon_days,
        "first_programme_start": first_start.isoformat() if first_start else None,
        "last_programme_time": (
            last_programme_time.isoformat() if last_programme_time else None
        ),
        "detected_provider_name": source_name or source_info_host,
        "suggested_provider_name": suggested,
    }


def validate_xmltv_url(url: str) -> dict:
    """Bounded streaming XMLTV validation without returning the source URL."""
    deadline = time.monotonic() + VALIDATION_TOTAL_TIMEOUT_SECONDS
    try:
        with tempfile.SpooledTemporaryFile(
            max_size=VALIDATION_SPOOL_MEMORY_BYTES, mode="w+b"
        ) as downloaded:
            _stream_download(url, downloaded, deadline)
            downloaded.seek(0)
            compressed = downloaded.read(2) == b"\x1f\x8b"
            downloaded.seek(0)
            stream = (
                gzip.GzipFile(fileobj=downloaded, mode="rb")
                if compressed else downloaded
            )
            try:
                limited = _LimitedReader(stream, MAX_XML_BYTES, deadline)
                return _inspect_xmltv_stream(limited, url)
            finally:
                if compressed:
                    stream.close()
    except SourceValidationError:
        raise
    except ET.ParseError:
        raise SourceValidationError("Invalid XML")
    except (gzip.BadGzipFile, EOFError):
        raise SourceValidationError("Invalid gzip-compressed XMLTV")
    except (OSError, ValueError):
        raise SourceValidationError("Could not validate source")
