"""One logical EPGTalk catalog backed by national and local XMLTV files."""

from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET


_CHANNEL_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def read_channel_definitions(path: str | Path, source: str) -> list[dict]:
    """Read channel metadata only, retaining all display-name aliases."""
    path = Path(path)
    if not path.is_file():
        return []
    channels = []
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag == "channel":
            channel_id = element.get("id")
            if channel_id:
                display_names = _unique(
                    [
                        (name.text or "").strip()
                        for name in element.findall("display-name")
                    ]
                )
                numbers = _unique(
                    [name for name in display_names if _CHANNEL_NUMBER.fullmatch(name)]
                )
                icons = _unique(
                    [
                        icon.get("src", "").strip()
                        for icon in element.findall("icon")
                    ]
                )
                channels.append(
                    {
                        "channel_id": channel_id,
                        "display_name": (
                            display_names[0] if display_names else channel_id
                        ),
                        "display_names": display_names,
                        "channel_numbers": numbers,
                        "icons": icons,
                        "icon": icons[0] if icons else None,
                        "sources": [source],
                    }
                )
            element.clear()
        elif element.tag == "programme":
            element.clear()
    return channels


def combine_epgtalk_catalog(
    national_path: str | Path, local_path: str | Path
) -> dict:
    """Deduplicate exact IDs, rejecting material metadata disagreements."""
    national = read_channel_definitions(national_path, "epgtalk")
    local = read_channel_definitions(local_path, "epgtalk_local")
    by_id: dict[str, dict] = {}
    conflicts = []

    for candidate in [*national, *local]:
        channel_id = candidate["channel_id"]
        existing = by_id.get(channel_id)
        if existing is None:
            by_id[channel_id] = candidate
            continue

        differences = []
        if existing["channel_numbers"] != candidate["channel_numbers"]:
            differences.append("channel_number")
        if existing["icons"] != candidate["icons"]:
            differences.append("icon")
        if differences:
            conflicts.append(
                {
                    "channel_id": channel_id,
                    "differences": differences,
                    "definitions": [existing, candidate],
                }
            )
            del by_id[channel_id]
            continue

        existing["display_names"] = _unique(
            existing["display_names"] + candidate["display_names"]
        )
        existing["sources"] = _unique(
            existing["sources"] + candidate["sources"]
        )

    channels = sorted(
        by_id.values(),
        key=lambda item: (
            item["display_name"].casefold(), item["channel_id"].casefold()
        ),
    )
    return {
        "channels": channels,
        "conflicts": conflicts,
        "national_count": len(national),
        "local_count": len(local),
        "combined_count": len(channels),
    }
