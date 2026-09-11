"""Persistent, last-known-good XMLTV source refreshes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from app.source_settings import (
    DEFAULT_BASELINE_SOURCE_ID,
    load_source_settings,
    schedule_cache_filename,
    validate_source_settings,
)
from app.version import __version__


DATA_DIR = Path("/data")
STATUS_PATH = DATA_DIR / "source-status.json"


@dataclass(frozen=True)
class SourceSpec:
    name: str
    filename: str
    url: str | None = None
    url_environment: str | None = None
    gzip_compressed: bool = False

    @property
    def path(self) -> Path:
        return DATA_DIR / self.filename

    def resolved_url(self) -> str:
        value = (
            os.environ.get(self.url_environment, "")
            if self.url_environment
            else self.url
        )
        if not value:
            raise SourceRefreshError(
                f"{self.url_environment or 'source URL'} is not configured"
            )
        return value


def current_source_specs() -> tuple[SourceSpec, ...]:
    """Resolve registered schedule feeds plus fixed enrichment feeds."""
    settings = validate_source_settings(load_source_settings())
    schedule_specs = tuple(
        SourceSpec(
            source_id,
            schedule_cache_filename(source_id),
            url=source["url"],
        )
        for source_id, source in settings["schedule_sources"].items()
    )
    return schedule_specs + (
        SourceSpec(
            "epgshare",
            "epgshare.xml",
            url=settings["enrichment_1"]["url"],
        ),
        SourceSpec(
            "epgtalk",
            "epgtalk.xml",
            url=settings["enrichment_2"]["primary_url"],
        ),
        SourceSpec(
            "epgtalk_local",
            "epgtalk_local.xml",
            url=settings["enrichment_2"]["secondary_url"],
        ),
    )


def _ensure_legacy_default_cache(specs: tuple[SourceSpec, ...]) -> None:
    """Seed the renamed default cache without removing the 0.5.x LKG file."""
    target = next(
        (spec.path for spec in specs if spec.name == DEFAULT_BASELINE_SOURCE_ID),
        None,
    )
    legacy = DATA_DIR / "hive.xml"
    if target is not None and not target.exists() and legacy.is_file():
        shutil.copy2(legacy, target)


_REFRESH_LOCK = threading.Lock()


class SourceRefreshError(Exception):
    """A refresh error whose message is safe to return to clients."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def inspect_xmltv(path: Path) -> dict:
    """Validate an XMLTV document and collect compact coverage metrics."""
    root_tag = None
    channel_count = 0
    programme_count = 0
    first_programme_start = None
    final_programme_stop = None

    for event, element in ET.iterparse(path, events=("start", "end")):
        tag = _local_name(element.tag)
        if root_tag is None and event == "start":
            root_tag = tag
            if root_tag != "tv":
                raise SourceRefreshError("XML root element is not <tv>")
        if event == "end" and tag == "channel":
            channel_count += 1
        elif event == "end" and tag == "programme":
            programme_count += 1
            if first_programme_start is None:
                first_programme_start = element.get("start")
            final_programme_stop = element.get("stop")
        if event == "end":
            element.clear()

    if root_tag != "tv":
        raise SourceRefreshError("XML root element is not <tv>")
    return {
        "file_size": path.stat().st_size,
        "channel_count": channel_count,
        "programme_count": programme_count,
        "first_programme_start": first_programme_start,
        "final_programme_stop": final_programme_stop,
    }


def _load_state() -> dict:
    try:
        state = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if isinstance(state, dict) and isinstance(state.get("sources"), dict):
            return state
    except (OSError, json.JSONDecodeError):
        pass
    return {"sources": {}}


def _fsync_directory() -> None:
    directory = os.open(DATA_DIR, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write_state(state: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".source-status.", suffix=".tmp", dir=DATA_DIR
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(state, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, STATUS_PATH)
        _fsync_directory()
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _safe_error(error: Exception) -> str:
    if isinstance(error, SourceRefreshError):
        return str(error)
    if isinstance(error, HTTPError):
        return f"HTTP request failed with status {error.code}"
    if isinstance(error, (gzip.BadGzipFile, EOFError)):
        return "download was not valid gzip data"
    if isinstance(error, ET.ParseError):
        return f"download was not valid XML: {error}"
    return f"refresh failed ({type(error).__name__})"


def _cached_result(spec: SourceSpec, state: dict) -> dict:
    saved = state["sources"].get(spec.name)
    if isinstance(saved, dict) and spec.path.exists():
        result = dict(saved)
        result["file_size"] = spec.path.stat().st_size
        return result
    if spec.path.exists():
        try:
            metrics = inspect_xmltv(spec.path)
            return {
                "source": spec.name,
                "success": True,
                "last_successful_refresh": datetime.fromtimestamp(
                    spec.path.stat().st_mtime, timezone.utc
                ).isoformat(),
                **metrics,
                "error": None,
            }
        except Exception as error:
            return {
                "source": spec.name,
                "success": False,
                "last_successful_refresh": None,
                "file_size": spec.path.stat().st_size,
                "channel_count": 0,
                "programme_count": 0,
                "first_programme_start": None,
                "final_programme_stop": None,
                "error": _safe_error(error),
            }
    return {
        "source": spec.name,
        "success": False,
        "last_successful_refresh": None,
        "file_size": 0,
        "channel_count": 0,
        "programme_count": 0,
        "first_programme_start": None,
        "final_programme_stop": None,
        "error": "no cached source file",
    }


def _download(spec: SourceSpec, destination: Path) -> None:
    request = Request(
        spec.resolved_url(),
        headers={"User-Agent": f"WonkEPG/{__version__}", "Accept-Encoding": "identity"},
    )
    with urlopen(request, timeout=120) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise SourceRefreshError(f"HTTP request failed with status {status}")
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())


def _prepare_xml(spec: SourceSpec, downloaded: Path, destination: Path) -> None:
    with downloaded.open("rb") as source:
        has_gzip_signature = source.read(2) == b"\x1f\x8b"

    opener = gzip.open if has_gzip_signature else open
    with opener(downloaded, "rb") as source, destination.open("wb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(destination, 0o644)


def _refresh_one(spec: SourceSpec, state: dict) -> dict:
    download_descriptor, download_name = tempfile.mkstemp(
        prefix=f".{spec.name}.", suffix=".download.tmp", dir=DATA_DIR
    )
    os.close(download_descriptor)
    xml_descriptor, xml_name = tempfile.mkstemp(
        prefix=f".{spec.name}.", suffix=".xml.tmp", dir=DATA_DIR
    )
    os.close(xml_descriptor)
    downloaded = Path(download_name)
    prepared = Path(xml_name)
    try:
        _download(spec, downloaded)
        _prepare_xml(spec, downloaded, prepared)
        metrics = inspect_xmltv(prepared)
        os.replace(prepared, spec.path)
        _fsync_directory()
        return {
            "source": spec.name,
            "success": True,
            "last_successful_refresh": datetime.now(timezone.utc).isoformat(),
            **metrics,
            "error": None,
        }
    except Exception as error:
        result = _cached_result(spec, state)
        result["success"] = False
        result["error"] = _safe_error(error)
        return result
    finally:
        downloaded.unlink(missing_ok=True)
        prepared.unlink(missing_ok=True)


def refresh_sources() -> dict:
    """Refresh every source independently, preserving each cached good file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _REFRESH_LOCK:
        state = _load_state()
        specs = current_source_specs()
        _ensure_legacy_default_cache(specs)
        results = []
        for spec in specs:
            result = _refresh_one(spec, state)
            state["sources"][spec.name] = result
            results.append(result)
        _write_state(state)
    return {"sources": results}


def source_status() -> dict:
    """Return persisted refresh results without downloading source data."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _REFRESH_LOCK:
        state = _load_state()
        specs = current_source_specs()
        _ensure_legacy_default_cache(specs)
        results = [_cached_result(spec, state) for spec in specs]
        if any(spec.name not in state["sources"] for spec in specs):
            state["sources"] = {result["source"]: result for result in results}
            _write_state(state)
    return {"sources": results}
