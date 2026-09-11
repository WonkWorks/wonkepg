"""Optional cache-only completion of missing XMLTV season identity."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from app.channel_merge import normalize_title_decorations
from app.tvmaze_provider import ProviderError, TVmazeProvider


CONFIG_PATH = Path("/app/config/episode_resolvers.json")
CACHE_ROOT = Path("/data/tvmaze")
STATE_PATH = Path("/data/episode-resolver-status.json")
OUTPUT_XMLTV = Path("/output/xmltv.xml")
CONFIG_SCHEMA_VERSION = 1
STATE_DETAIL_LIMIT = 500
STRICT_EPISODE_MATCH = "strict_episode_match"
DATE_ANCHORED_IDENTITY = "date_anchored_identity"
RESOLUTION_MODES = {STRICT_EPISODE_MATCH, DATE_ANCHORED_IDENTITY}
MATCH_TOLERANCE_MINUTES = 2
_EPISODE_ONLY = re.compile(r"^E([1-9]\d*)$", re.IGNORECASE)
_SEASON_EPISODE = re.compile(
    r"^S[1-9]\d*\s*E[1-9]\d*$", re.IGNORECASE
)
_GENERIC_EPISODE_NAME = re.compile(r"^Episode\s+\d+$", re.IGNORECASE)
_DATE_ANCHORED_BLOCKED_CATEGORIES = {
    "movie", "movies", "sport", "sports", "special", "specials"
}
_DATE_ANCHORED_BLOCKED_WORD = re.compile(r"\b(?:movies?|sports?|specials?)\b")
_REFRESH_LOCK = threading.Lock()


def normalized_title(value: object) -> str:
    canonical = normalize_title_decorations(str(value or "")).canonical_title
    return re.sub(r"\s+", " ", canonical).strip().casefold()


def binding_key(channel_id: object, title: object) -> str:
    return f"{str(channel_id or '').strip()}|{normalized_title(title)}"


def default_config() -> dict:
    return {"schema_version": CONFIG_SCHEMA_VERSION, "bindings": {}}


def _validated_binding(key: object, value: object) -> dict:
    if not isinstance(key, str) or "|" not in key or not isinstance(value, dict):
        raise ValueError("invalid resolver binding")
    channel_id, title = key.split("|", 1)
    if not re.fullmatch(r"wonk\.\d+", channel_id) or not title.strip():
        raise ValueError("invalid resolver binding key")
    if value.get("provider") != "tvmaze":
        raise ValueError("unsupported resolver provider")
    show_id = value.get("show_id")
    if isinstance(show_id, bool) or not isinstance(show_id, int) or show_id < 1:
        raise ValueError("resolver show ID must be a positive integer")
    aliases = value.get("aliases", [])
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) and alias.strip() for alias in aliases
    ):
        raise ValueError("resolver aliases must be non-empty strings")
    mode = value.get("mode", STRICT_EPISODE_MATCH)
    if mode not in RESOLUTION_MODES:
        raise ValueError("unsupported episode resolution mode")
    timezone_name = str(value.get("timezone") or "America/New_York")
    try:
        ZoneInfo(timezone_name)
    except Exception as error:
        raise ValueError("invalid resolver timezone") from error
    return {
        "provider": "tvmaze",
        "show_id": show_id,
        "confirmed": value.get("confirmed") is True,
        "canonical_name": str(value.get("canonical_name") or ""),
        "aliases": list(dict.fromkeys(normalized_title(alias) for alias in aliases)),
        "mode": mode,
        "timezone": timezone_name,
    }


def validate_config(document: object) -> dict:
    if not isinstance(document, dict):
        raise ValueError("resolver configuration must be an object")
    if document.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("unsupported resolver configuration schema")
    bindings = document.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("resolver bindings must be an object")
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "bindings": {
            key: _validated_binding(key, value)
            for key, value in bindings.items()
        },
    }


def load_config(path: str | Path = CONFIG_PATH) -> dict:
    path = Path(path)
    if not path.exists():
        return default_config()
    try:
        with path.open("r", encoding="utf-8") as source:
            return validate_config(json.load(source))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("resolver configuration is unreadable") from error


def _atomic_json(path: Path, document: dict, backup_name: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and backup_name:
        backup = path.with_name(backup_name)
        if not backup.exists():
            shutil.copy2(path, backup)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(document, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def save_binding(
    channel_id: str,
    title: str,
    show_id: int,
    canonical_name: str,
    provider: str = "tvmaze",
    mode: str = STRICT_EPISODE_MATCH,
    timezone_name: str = "America/New_York",
    path: str | Path = CONFIG_PATH,
) -> dict:
    key = binding_key(channel_id, title)
    proposed = {
        "provider": provider,
        "show_id": show_id,
        "confirmed": True,
        "canonical_name": canonical_name,
        "aliases": [],
        "mode": mode,
        "timezone": timezone_name,
    }
    _validated_binding(key, proposed)
    path = Path(path)
    config = load_config(path)
    config["bindings"][key] = proposed
    validated = validate_config(config)
    _atomic_json(path, validated, "episode_resolvers.json.pre-0.7.1.bak")
    return validated["bindings"][key]


def _provider(provider_id: str, cache_root: str | Path = CACHE_ROOT):
    if provider_id == "tvmaze":
        return TVmazeProvider(cache_root)
    raise ValueError("unsupported resolver provider")


def search_shows(query: str, provider: str = "tvmaze") -> list[dict]:
    return _provider(provider).search_shows(query)


def refresh_confirmed_catalogs(
    config_path: str | Path = CONFIG_PATH,
    cache_root: str | Path = CACHE_ROOT,
) -> dict:
    if not _REFRESH_LOCK.acquire(blocking=False):
        return {"status": "already_running", "refreshed": [], "failed": []}
    try:
        config = load_config(config_path)
        unique = {}
        for binding in config["bindings"].values():
            if binding["confirmed"]:
                unique[(binding["provider"], binding["show_id"])] = binding
        refreshed, failed = [], []
        for (provider_id, show_id), binding in unique.items():
            try:
                result = _provider(provider_id, cache_root).refresh_catalog(
                    show_id, binding["canonical_name"]
                )
                refreshed.append(result)
            except Exception as error:
                failed.append({
                    "provider": provider_id,
                    "show_id": show_id,
                    "error": f"refresh failed ({type(error).__name__})",
                })
        return {
            "status": "complete" if not failed else "degraded",
            "refreshed": refreshed,
            "failed": failed,
        }
    finally:
        _REFRESH_LOCK.release()


def _episode_only_identity(programme: ET.Element) -> int | None:
    episode_elements = programme.findall("episode-num")
    if not episode_elements:
        return None
    if any(item.get("system") == "xmltv_ns" for item in episode_elements):
        return None
    onscreen = [
        (item.text or "").strip()
        for item in episode_elements
        if item.get("system") == "onscreen"
    ]
    if len(onscreen) != 1 or _SEASON_EPISODE.fullmatch(onscreen[0]):
        return None
    if any(item.get("system") != "onscreen" for item in episode_elements):
        return None
    match = _EPISODE_ONLY.fullmatch(onscreen[0])
    return int(match.group(1)) if match else None


def _programme_date(
    programme: ET.Element, timezone_name: str
) -> tuple[str, datetime]:
    value = programme.get("start", "")
    parsed = datetime.strptime(value, "%Y%m%d%H%M%S %z")
    local = parsed.astimezone(ZoneInfo(timezone_name))
    return local.date().isoformat(), local


def _duration_minutes(programme: ET.Element) -> int:
    start = datetime.strptime(programme.get("start", ""), "%Y%m%d%H%M%S %z")
    stop = datetime.strptime(programme.get("stop", ""), "%Y%m%d%H%M%S %z")
    return round((stop - start).total_seconds() / 60)


def _airtime_matches(value: object, local_start: datetime) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-2]\d:[0-5]\d", value):
        return False
    hour, minute = (int(part) for part in value.split(":"))
    if hour > 23:
        return False
    external_minutes = hour * 60 + minute
    local_minutes = local_start.hour * 60 + local_start.minute
    difference = abs(external_minutes - local_minutes)
    return min(difference, 24 * 60 - difference) <= MATCH_TOLERANCE_MINUTES


def _runtime_matches(value: object, programme: ET.Element) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
        and abs(value - _duration_minutes(programme))
        <= MATCH_TOLERANCE_MINUTES
    )


def _subtitle_conflicts(programme: ET.Element, episode: dict) -> bool:
    subtitle = normalized_title(programme.findtext("sub-title"))
    external_name = normalized_title(episode.get("name"))
    named_external = external_name and not _GENERIC_EPISODE_NAME.fullmatch(
        str(episode.get("name") or "").strip()
    )
    return bool(subtitle and named_external and subtitle != external_name)


def _blocked_date_anchored_category(programme: ET.Element) -> bool:
    categories = {
        normalized_title(item.text) for item in programme.findall("category")
    }
    return any(
        category in _DATE_ANCHORED_BLOCKED_CATEGORIES
        or _DATE_ANCHORED_BLOCKED_WORD.search(category)
        for category in categories
    )


def _complete_regular_episode(episode: dict) -> bool:
    return (
        episode.get("type") == "regular"
        and isinstance(episode.get("season"), int)
        and not isinstance(episode.get("season"), bool)
        and episode["season"] > 0
        and isinstance(episode.get("number"), int)
        and not isinstance(episode.get("number"), bool)
        and episode["number"] > 0
    )


def _evaluate_episode(
    programme: ET.Element,
    binding: dict,
    source_episode: int,
    local_start: datetime,
    on_date: list[dict],
) -> tuple[str, dict | None, list[str]]:
    if not on_date:
        return "no_episode_match", None, []
    if len(on_date) != 1:
        return "ambiguous_match", None, []
    episode = on_date[0]
    if not _complete_regular_episode(episode):
        return "no_episode_match", episode, []

    mode = binding["mode"]
    if mode == DATE_ANCHORED_IDENTITY:
        if _blocked_date_anchored_category(programme):
            return "content_type_rejected", episode, []
        if not _airtime_matches(episode.get("airtime"), local_start):
            return "airtime_mismatch", episode, []
        if not _runtime_matches(episode.get("runtime"), programme):
            return "runtime_mismatch", episode, []
    else:
        if episode["number"] != source_episode:
            return "episode_number_conflict", episode, []
        if episode.get("runtime") is not None and not _runtime_matches(
            episode.get("runtime"), programme
        ):
            return "runtime_mismatch", episode, []

    if _subtitle_conflicts(programme, episode):
        return "subtitle_conflict", episode, []

    evidence = [
        "confirmed_show_binding",
        "exact_airdate",
        "unique_episode_on_date",
    ]
    if _airtime_matches(episode.get("airtime"), local_start):
        evidence.append("airtime_match")
    if _runtime_matches(episode.get("runtime"), programme):
        evidence.append("runtime_match")
    if mode == STRICT_EPISODE_MATCH:
        evidence.append("episode_number_agreement")
    return "resolved", episode, evidence

def _insert_xmltv_ns(programme: ET.Element, season: int, episode: int) -> None:
    if programme.find("episode-num[@system='xmltv_ns']") is not None:
        return
    structured = ET.Element("episode-num", {"system": "xmltv_ns"})
    structured.text = f"{season - 1}.{episode - 1}."
    children = list(programme)
    episode_positions = [
        index for index, child in enumerate(children) if child.tag == "episode-num"
    ]
    position = episode_positions[-1] + 1 if episode_positions else len(children)
    programme.insert(position, structured)


def _detail(programme: ET.Element, binding: dict, status: str, **extra) -> dict:
    return {
        "channel": programme.get("channel"),
        "start": programme.get("start"),
        "title": programme.findtext("title") or "",
        "provider": binding["provider"],
        "show_id": binding["show_id"],
        "status": status,
        **extra,
    }


def _binding_for(config: dict, programme: ET.Element) -> dict | None:
    channel = programme.get("channel", "")
    title = normalized_title(programme.findtext("title"))
    direct = config["bindings"].get(f"{channel}|{title}")
    if direct and direct["confirmed"]:
        return direct
    for key, binding in config["bindings"].items():
        bound_channel, _ = key.split("|", 1)
        if bound_channel == channel and binding["confirmed"] and title in binding["aliases"]:
            return binding
    return None


def apply_cached_episode_resolution(
    programme_groups: list[list[ET.Element]],
    config_path: str | Path = CONFIG_PATH,
    cache_root: str | Path = CACHE_ROOT,
    state_path: str | Path = STATE_PATH,
) -> dict:
    """Add corroborated xmltv_ns values using local cache only; never raise."""
    counts = Counter()
    details: list[dict] = []
    try:
        config = load_config(config_path)
    except Exception as error:
        config = default_config()
        counts["resolver_unavailable"] += 1
        details.append({
            "status": "resolver_unavailable",
            "error": f"configuration unavailable ({type(error).__name__})",
        })
    counts["confirmed_bindings"] = sum(
        binding["confirmed"] for binding in config["bindings"].values()
    )
    catalog_cache = {}
    provider_cache = {}
    resolved_anchors = {}
    repeat_candidates = []

    for programme in (item for group in programme_groups for item in group):
        source_episode = _episode_only_identity(programme)
        if source_episode is None:
            counts["ineligible_records"] += 1
            continue
        counts["candidate_records"] += 1
        binding = _binding_for(config, programme)
        if binding is None:
            counts["no_binding_records"] += 1
            continue
        if programme.find("previously-shown") is not None:
            repeat_candidates.append((programme, binding, source_episode))
            continue
        catalog_key = (binding["provider"], binding["show_id"])
        if catalog_key not in catalog_cache:
            try:
                provider_cache[catalog_key] = _provider(
                    binding["provider"], cache_root
                )
                catalog_cache[catalog_key] = provider_cache[
                    catalog_key
                ].get_cached_episode_catalog(binding["show_id"])
            except ProviderError as error:
                catalog_cache[catalog_key] = error
        catalog = catalog_cache[catalog_key]
        if isinstance(catalog, Exception):
            status = (
                "no_cache" if "missing" in str(catalog) else "resolver_unavailable"
            )
            counts[f"{status}_records"] += 1
            if len(details) < STATE_DETAIL_LIMIT:
                details.append(_detail(programme, binding, status))
            continue
        try:
            airdate, local_start = _programme_date(
                programme, binding["timezone"]
            )
            on_date = provider_cache[catalog_key].resolve_season_for_episode(
                catalog, airdate
            )
            status, episode, evidence = _evaluate_episode(
                programme, binding, source_episode, local_start, on_date
            )
            if status == "resolved":
                _insert_xmltv_ns(
                    programme, episode["season"], episode["number"]
                )
                counts["resolved_records"] += 1
                counts[f"{binding['mode']}_records"] += 1
                anchor = (
                    programme.get("channel", ""),
                    normalized_title(programme.findtext("title")),
                    source_episode,
                )
                resolved_anchors[anchor] = {
                    "episode": episode,
                    "mode": binding["mode"],
                }
            else:
                count_key = {
                    "episode_number_conflict": "conflict_records",
                    "ambiguous_match": "ambiguous_match_records",
                    "no_episode_match": "no_episode_match_records",
                    "airtime_mismatch": "airtime_mismatch_records",
                    "runtime_mismatch": "runtime_mismatch_records",
                    "subtitle_conflict": "subtitle_conflict_records",
                    "content_type_rejected": "content_type_rejected_records",
                }[status]
                counts[count_key] += 1
            if len(details) < STATE_DETAIL_LIMIT:
                extra = {
                    "source_episode": source_episode,
                    "source_identity": {
                        "system": "onscreen",
                        "value": f"E{source_episode}",
                    },
                    "airdate": airdate,
                    "resolution_mode": binding["mode"],
                }
                if episode is not None:
                    extra.update({
                        "episode_id": episode.get("id"),
                        "external_episode": episode.get("number"),
                        "resolved_season": episode.get("season"),
                    })
                if status == "resolved":
                    extra.update({
                        "confidence": "high",
                        "evidence": evidence,
                        "resolved_identity": {
                            "provider": binding["provider"],
                            "show_id": binding["show_id"],
                            "episode_id": episode["id"],
                            "season": episode["season"],
                            "episode": episode["number"],
                            "mode": binding["mode"],
                            "confidence": "high",
                            "evidence": evidence,
                        },
                    })
                details.append(_detail(programme, binding, status, **extra))
        except Exception as error:
            counts["resolver_unavailable_records"] += 1
            if len(details) < STATE_DETAIL_LIMIT:
                details.append(_detail(
                    programme,
                    binding,
                    "resolver_unavailable",
                    error=f"resolution failed ({type(error).__name__})",
                ))

    for programme, binding, source_episode in repeat_candidates:
        anchor = (
            programme.get("channel", ""),
            normalized_title(programme.findtext("title")),
            source_episode,
        )
        resolution = resolved_anchors.get(anchor)
        if resolution is None:
            counts["repeat_without_original_records"] += 1
            if len(details) < STATE_DETAIL_LIMIT:
                details.append(_detail(
                    programme, binding, "no_episode_match",
                    source_episode=source_episode,
                    source_identity={
                        "system": "onscreen", "value": f"E{source_episode}"
                    },
                    resolution_mode=binding["mode"],
                    evidence=["repeat_without_resolved_original"],
                ))
            continue
        episode = resolution["episode"]
        mode = resolution["mode"]
        _insert_xmltv_ns(programme, episode["season"], episode["number"])
        counts["resolved_records"] += 1
        counts["resolved_repeat_records"] += 1
        counts[f"{mode}_records"] += 1
        evidence = [
            "confirmed_show_binding",
            "resolved_original_airing",
            "same_title_and_source_episode",
        ]
        if len(details) < STATE_DETAIL_LIMIT:
            details.append(_detail(
                programme,
                binding,
                "resolved",
                source_episode=source_episode,
                source_identity={
                    "system": "onscreen", "value": f"E{source_episode}"
                },
                episode_id=episode["id"],
                external_episode=episode["number"],
                resolved_season=episode["season"],
                resolution_mode=mode,
                confidence="high",
                evidence=evidence,
                resolved_identity={
                    "provider": binding["provider"],
                    "show_id": binding["show_id"],
                    "episode_id": episode["id"],
                    "season": episode["season"],
                    "episode": episode["number"],
                    "mode": mode,
                    "confidence": "high",
                    "evidence": evidence,
                },
            ))

    metric_names = (
        "candidate_records", "confirmed_bindings", "resolved_records",
        "resolved_repeat_records", "conflict_records", "no_binding_records",
        "no_cache_records", "resolver_unavailable_records",
        "no_episode_match_records", "ambiguous_match_records",
        "repeat_without_original_records", "ineligible_records",
        "strict_episode_match_records", "date_anchored_identity_records",
        "airtime_mismatch_records", "runtime_mismatch_records",
        "subtitle_conflict_records", "content_type_rejected_records",
    )
    report = {
        "provider": "tvmaze",
        "external_api_calls": 0,
        **{name: counts[name] for name in metric_names},
        "details": details,
    }
    try:
        _atomic_json(Path(state_path), report)
    except Exception:
        pass
    return report


def _candidate_summary(output_path: str | Path = OUTPUT_XMLTV) -> list[dict]:
    output_path = Path(output_path)
    if not output_path.exists():
        return []
    root = ET.parse(output_path).getroot()
    candidates = {}
    for programme in root.findall("programme"):
        episode = _episode_only_identity(programme)
        if episode is None:
            continue
        key = binding_key(programme.get("channel"), programme.findtext("title"))
        item = candidates.setdefault(key, {
            "key": key,
            "channel": programme.get("channel"),
            "title": programme.findtext("title") or "",
            "records": 0,
        })
        item["records"] += 1
    return sorted(candidates.values(), key=lambda item: (item["channel"], item["title"]))


def resolver_status(
    config_path: str | Path = CONFIG_PATH,
    cache_root: str | Path = CACHE_ROOT,
    state_path: str | Path = STATE_PATH,
    output_path: str | Path = OUTPUT_XMLTV,
) -> dict:
    config_error = None
    try:
        config = load_config(config_path)
    except ValueError as error:
        config, config_error = default_config(), str(error)
    bindings = []
    for key, binding in config["bindings"].items():
        cache_path = Path(cache_root) / f"show-{binding['show_id']}.json"
        bindings.append({"key": key, **binding, "cache_available": cache_path.exists()})
    try:
        with Path(state_path).open("r", encoding="utf-8") as source:
            last_build = json.load(source)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        last_build = None
    candidates = _candidate_summary(output_path)
    bound_keys = set(config["bindings"])
    return {
        "enabled": True,
        "provider": "tvmaze",
        "config_error": config_error,
        "confirmed_bindings": len(bindings),
        "bindings": bindings,
        "candidate_count": sum(item["records"] for item in candidates),
        "unresolved_shows": [
            item for item in candidates if item["key"] not in bound_keys
        ],
        "last_build": last_build,
    }
