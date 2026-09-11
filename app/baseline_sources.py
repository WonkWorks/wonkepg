"""Runtime catalogs and exact-ID resolution for canonical schedule sources."""

from __future__ import annotations

from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from app.source_settings import (
    DEFAULT_BASELINE_SOURCE_ID,
    load_source_settings,
    schedule_cache_filename,
)


DATA_DIR = Path("/data")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def schedule_source_registry(settings: dict | None = None) -> dict:
    """Return credential-free runtime definitions keyed by stable source ID."""
    settings = settings or load_source_settings()
    default_source = settings["baseline"]["default_source"]
    registry = {
        source_id: {
            "source_id": source_id,
            "provider_name": source.get("provider_name") or source_id,
            "default": source_id == default_source,
            "path": DATA_DIR / schedule_cache_filename(source_id),
        }
        for source_id, source in settings["schedule_sources"].items()
    }
    legacy_target = registry.get(DEFAULT_BASELINE_SOURCE_ID, {}).get("path")
    legacy_path = DATA_DIR / "hive.xml"
    if (
        legacy_target is not None
        and not legacy_target.exists()
        and legacy_path.is_file()
    ):
        shutil.copy2(legacy_path, legacy_target)
    return registry


def read_schedule_catalog(path: str | Path) -> list[dict]:
    """Read sanitized channel definitions without retaining programme data."""
    channels = []
    for _, element in ET.iterparse(path, events=("end",)):
        tag = _local_name(element.tag)
        if tag == "channel":
            channel_id = element.get("id")
            if channel_id:
                display_names = list(dict.fromkeys(
                    (child.text or "").strip()
                    for child in element
                    if _local_name(child.tag) == "display-name"
                    and (child.text or "").strip()
                ))
                channels.append({
                    "channel_id": channel_id,
                    "display_name": display_names[0] if display_names else channel_id,
                    "display_names": display_names,
                })
            element.clear()
        elif tag == "programme":
            element.clear()
    channels.sort(key=lambda item: (
        item["display_name"].casefold(), item["channel_id"].casefold()
    ))
    return channels


def load_schedule_catalogs(settings: dict | None = None) -> dict:
    """Load every configured source's usable cached channel catalog."""
    catalogs = {}
    for source_id, source in schedule_source_registry(settings).items():
        try:
            channels = read_schedule_catalog(source["path"])
            catalogs[source_id] = {
                **source,
                "available": True,
                "channels": channels,
                "channel_ids": {item["channel_id"] for item in channels},
            }
        except (OSError, ET.ParseError):
            catalogs[source_id] = {
                **source,
                "available": False,
                "channels": [],
                "channel_ids": set(),
            }
    return catalogs


def inspect_baseline_mappings(
    matrix: dict, catalogs: dict | None = None
) -> dict:
    """Classify persisted baseline pairs against current/LKG exact catalogs."""
    catalogs = catalogs or load_schedule_catalogs()
    stale = []
    valid = {}
    for row in matrix.get("channels", []):
        number = str(row.get("number"))
        selection = row.get("baseline")
        if not isinstance(selection, dict):
            continue
        source_id = selection.get("source")
        channel_id = selection.get("channel_id")
        if not source_id or not channel_id:
            continue
        source = catalogs.get(source_id)
        reason = None
        if source is None:
            reason = "missing_source"
        elif not source["available"]:
            reason = "source_unavailable"
        elif channel_id not in source["channel_ids"]:
            reason = "missing_channel"
        else:
            valid[number] = {
                "source": source_id,
                "channel_id": channel_id,
                "path": source["path"],
            }
        if reason:
            stale.append({
                "number": number,
                "source": source_id,
                "channel_id": channel_id,
                "reason": reason,
            })
    return {
        "stale_baseline_count": len(stale),
        "stale_baseline_mappings": stale,
        "valid_baseline_mappings": valid,
    }


def public_schedule_sources(catalogs: dict | None = None) -> list[dict]:
    """Return registry metadata safe for browser catalog discovery."""
    catalogs = catalogs or load_schedule_catalogs()
    return [
        {
            "source_id": source_id,
            "provider_name": source["provider_name"],
            "default": source["default"],
            "available": source["available"],
            "channel_count": len(source["channels"]),
        }
        for source_id, source in catalogs.items()
    ]
