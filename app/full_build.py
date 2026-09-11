"""Full persisted-matrix XMLTV build orchestration."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

from app.channel_merge import (
    EPGSHARE_XMLTV,
    EPGTALK_LOCAL_XMLTV,
    EPGTALK_XMLTV,
    HIVE_XMLTV,
    ChannelMergeResult,
    create_output_channel,
    load_selected_programmes,
    merge_single_channel,
)
from app.epgtalk_catalog import combine_epgtalk_catalog
from app.baseline_sources import (
    inspect_baseline_mappings,
    load_schedule_catalogs,
)
from app.episode_resolver import apply_cached_episode_resolution


FULL_OUTPUT_XMLTV = Path("/output/xmltv.xml")

def _selection_channel_id(selection) -> str | None:
    if isinstance(selection, str):
        return selection or None
    if isinstance(selection, dict):
        return selection.get("channel_id") or None
    return None


def enrichment_ids_for_row(channel_row: dict) -> tuple[str | None, str | None]:
    epgshare_id = _selection_channel_id(channel_row.get("enrichment_1"))
    epgtalk_id = _selection_channel_id(channel_row.get("enrichment_2"))
    return epgshare_id, epgtalk_id


def _write_combined_xmltv(
    channels: list[ET.Element],
    programme_groups: list[list[ET.Element]],
    output_path: Path,
) -> dict:
    root = ET.Element(
        "tv", {"generator-info-name": "WonkEPG full matrix build"}
    )
    for channel in channels:
        root.append(deepcopy(channel))
    for programmes in programme_groups:
        for programme in programmes:
            root.append(deepcopy(programme))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.tmp")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(temporary_path, encoding="utf-8", xml_declaration=True)

    try:
        ET.parse(temporary_path)
    except (ET.ParseError, OSError) as error:
        temporary_path.unlink(missing_ok=True)
        return {"valid": False, "error": str(error)}

    temporary_path.replace(output_path)
    return {"valid": True, "error": None}


def _timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H%M%S %z")


def _channel_ids(path: Path) -> set[str]:
    """Load exact XMLTV channel-definition IDs from an available cache."""
    if not path.is_file():
        return set()
    channel_ids = set()
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag.rsplit("}", 1)[-1] == "channel" and element.get("id"):
            channel_ids.add(element.get("id"))
        element.clear()
    return channel_ids


def inspect_stale_mappings(
    matrix: dict,
    epgshare_available: set[str] | None = None,
    epgtalk_available: set[str] | None = None,
    schedule_catalogs: dict | None = None,
) -> dict:
    """Report saved IDs absent from current cached source definitions."""
    if epgshare_available is None:
        epgshare_available = _channel_ids(EPGSHARE_XMLTV)
    if epgtalk_available is None:
        definitions = combine_epgtalk_catalog(
            EPGTALK_XMLTV, EPGTALK_LOCAL_XMLTV
        )
        epgtalk_available = {
            item["channel_id"] for item in definitions["channels"]
        } | {
            item["channel_id"] for item in definitions["conflicts"]
        }

    stale_epgshare = []
    stale_epgtalk = []
    for row in matrix.get("channels", []):
        number = str(row.get("number"))
        epgshare_id, epgtalk_id = enrichment_ids_for_row(row)
        if epgshare_id and epgshare_id not in epgshare_available:
            stale_epgshare.append(
                {"number": number, "channel_id": epgshare_id}
            )
        if epgtalk_id and epgtalk_id not in epgtalk_available:
            stale_epgtalk.append(
                {"number": number, "channel_id": epgtalk_id}
            )
    baseline = inspect_baseline_mappings(matrix, schedule_catalogs)
    return {
        "stale_baseline_count": baseline["stale_baseline_count"],
        "stale_baseline_mappings": baseline["stale_baseline_mappings"],
        "stale_epgshare_count": len(stale_epgshare),
        "stale_epgtalk_count": len(stale_epgtalk),
        "stale_epgshare_mappings": stale_epgshare,
        "stale_epgtalk_mappings": stale_epgtalk,
    }


def build_full_xmltv(
    matrix: dict, schedule_catalogs: dict | None = None
) -> dict:
    """Build all persisted rows while isolating per-channel failures."""
    rows = matrix.get("channels", [])
    resolved_rows = []
    epgshare_ids: set[str] = set()
    epgtalk_ids: set[str] = set()

    schedule_catalogs = schedule_catalogs or load_schedule_catalogs()
    baseline_report = inspect_baseline_mappings(matrix, schedule_catalogs)
    valid_baselines = baseline_report["valid_baseline_mappings"]
    baseline_ids: dict[str, set[str]] = {}

    for row in rows:
        number = str(row.get("number"))
        baseline = valid_baselines.get(number)
        epgshare_id, epgtalk_id = enrichment_ids_for_row(row)
        resolved_rows.append((row, baseline, epgshare_id, epgtalk_id))
        if baseline:
            baseline_ids.setdefault(baseline["source"], set()).add(
                baseline["channel_id"]
            )
        if epgshare_id:
            epgshare_ids.add(epgshare_id)
        if epgtalk_id:
            epgtalk_ids.add(epgtalk_id)

    epgshare_available = _channel_ids(EPGSHARE_XMLTV)
    epgtalk_definition_catalog = combine_epgtalk_catalog(
        EPGTALK_XMLTV, EPGTALK_LOCAL_XMLTV
    )
    epgtalk_conflicts = {
        item["channel_id"]
        for item in epgtalk_definition_catalog["conflicts"]
    }
    epgtalk_available = {
        item["channel_id"]
        for item in epgtalk_definition_catalog["channels"]
    } | epgtalk_conflicts
    stale_report = inspect_stale_mappings(
        matrix, epgshare_available, epgtalk_available, schedule_catalogs
    )
    stale_baselines = {
        item["number"]: item
        for item in stale_report["stale_baseline_mappings"]
    }
    stale_epgshare = {
        (item["number"], item["channel_id"])
        for item in stale_report["stale_epgshare_mappings"]
    }
    stale_epgtalk = {
        (item["number"], item["channel_id"])
        for item in stale_report["stale_epgtalk_mappings"]
    }
    valid_epgshare_ids = epgshare_ids & epgshare_available
    valid_epgtalk_ids = epgtalk_ids & epgtalk_available

    baseline_programmes = {
        source_id: load_selected_programmes(
            schedule_catalogs[source_id]["path"], channel_ids
        )
        for source_id, channel_ids in baseline_ids.items()
    }
    epgshare_catalog = load_selected_programmes(
        EPGSHARE_XMLTV, valid_epgshare_ids
    )
    epgtalk_national_catalog = load_selected_programmes(
        EPGTALK_XMLTV, valid_epgtalk_ids
    )
    epgtalk_local_catalog = load_selected_programmes(
        EPGTALK_LOCAL_XMLTV, valid_epgtalk_ids
    )

    epgtalk_catalog = {}
    for channel_id in valid_epgtalk_ids:
        if channel_id in epgtalk_conflicts:
            continue
        national_programmes = epgtalk_national_catalog.get(channel_id, [])
        local_programmes = epgtalk_local_catalog.get(channel_id, [])
        # A safely deduplicated ID is one logical channel. Use one complete
        # schedule, never a union; programme count provides a neutral choice
        # when both cached feeds currently contain the same exact ID.
        epgtalk_catalog[channel_id] = max(
            (national_programmes, local_programmes), key=len
        )

    output_channels: list[ET.Element] = []
    programme_groups: list[list[ET.Element]] = []
    per_channel_diagnostics = []
    all_programmes: list[ET.Element] = []

    channels_with_programmes = 0
    channels_with_no_schedule = 0
    channels_degraded_to_baseline_only = 0
    total_baseline_duplicates = 0
    total_boundary_trims = 0
    total_baseline_fragments_dropped = 0
    baseline_fragment_drop_details = []
    total_same_start_conflicts = 0
    total_unresolved_conflicts = 0
    total_epgtalk_extensions = 0
    total_collisions = 0
    total_successful_enrichment_matches = 0
    total_standard_matches = 0
    total_padding_tolerant_matches = 0
    total_ambiguous_relaxed_matches_rejected = 0
    total_duration_mismatch_rejected = 0
    total_start_mismatch_rejected = 0
    total_low_overlap_rejected = 0
    total_metadata_conflict_rejected = 0
    trim_duration_distribution: dict[str, int] = {}
    channel_trim_counts = []
    implausibly_short_programmes = []
    implausibly_short_threshold_seconds = 300

    for row, baseline, selected_epgshare_id, selected_epgtalk_id in resolved_rows:
        number = str(row.get("number"))
        name = row.get("name")
        output_channels.append(create_output_channel(row))
        warnings = []
        stale_for_channel = []
        epgshare_id = selected_epgshare_id
        epgtalk_id = selected_epgtalk_id
        stale_baseline = stale_baselines.get(number)
        if stale_baseline:
            channels_with_no_schedule += 1
            programme_groups.append([])
            stale_for_channel.append({
                "source": "baseline",
                "source_id": stale_baseline["source"],
                "channel_id": stale_baseline["channel_id"],
                "reason": stale_baseline["reason"],
            })
            per_channel_diagnostics.append({
                "number": number,
                "name": name,
                "status": "no_schedule",
                "programme_count": 0,
                "warnings": [
                    "selected baseline schedule is unavailable; "
                    "enrichment was not promoted"
                ],
                "stale_mappings": stale_for_channel,
            })
            continue
        if (number, selected_epgshare_id) in stale_epgshare:
            epgshare_id = None
            stale_for_channel.append(
                {
                    "source": "epgshare",
                    "channel_id": selected_epgshare_id,
                }
            )
            warnings.append(
                "Enrichment 1 selected channel not present in current source"
            )
        if (number, selected_epgtalk_id) in stale_epgtalk:
            epgtalk_id = None
            stale_for_channel.append(
                {
                    "source": "epgtalk",
                    "channel_id": selected_epgtalk_id,
                }
            )
            warnings.append(
                "Enrichment 2 selected channel not present in current source"
            )
        if (
            baseline
            and stale_for_channel
            and not epgshare_id
            and not epgtalk_id
        ):
            channels_degraded_to_baseline_only += 1

        if selected_epgtalk_id in epgtalk_conflicts:
            channels_with_no_schedule += 1
            programme_groups.append([])
            per_channel_diagnostics.append(
                {
                    "number": number,
                    "name": name,
                    "status": "error",
                    "programme_count": 0,
                    "errors": [
                        "selected Enrichment 2 channel has an unresolved "
                        "national/local definition conflict"
                    ],
                    "warnings": warnings,
                    "stale_mappings": stale_for_channel,
                }
            )
            continue

        if not baseline and not epgtalk_id:
            reason = "no valid baseline or Enrichment 2 schedule"
            if epgshare_id:
                reason += "; Enrichment 1 is enrichment-only"
            channels_with_no_schedule += 1
            programme_groups.append([])
            per_channel_diagnostics.append(
                {
                    "number": number,
                    "name": name,
                    "status": "no_schedule",
                    "programme_count": 0,
                    "warnings": [reason, *warnings],
                    "stale_mappings": stale_for_channel,
                }
            )
            continue

        try:
            result: ChannelMergeResult = merge_single_channel(
                channel_row=row,
                baseline_xmltv=(baseline["path"] if baseline else None),
                baseline_channel_id=(
                    baseline["channel_id"] if baseline else None
                ),
                baseline_programmes=(
                    baseline_programmes[baseline["source"]].get(
                        baseline["channel_id"], []
                    )
                    if baseline else []
                ),
                epgshare_channel_id=epgshare_id,
                epgtalk_channel_id=epgtalk_id,
                epgshare_xmltv=EPGSHARE_XMLTV,
                epgtalk_xmltv=EPGTALK_XMLTV,
                epgshare_programmes=(
                    epgshare_catalog.get(epgshare_id, [])
                    if epgshare_id
                    else None
                ),
                epgtalk_programmes=(
                    epgtalk_catalog.get(epgtalk_id, [])
                    if epgtalk_id
                    else None
                ),
            )
        except Exception as error:
            channels_with_no_schedule += 1
            programme_groups.append([])
            per_channel_diagnostics.append(
                {
                    "number": number,
                    "name": name,
                    "status": "error",
                    "programme_count": 0,
                    "errors": [
                        f"channel build failed ({type(error).__name__})"
                    ],
                    "warnings": warnings,
                    "stale_mappings": stale_for_channel,
                }
            )
            continue

        diagnostics = result.diagnostics
        programmes = result.programmes
        programme_groups.append(programmes)
        all_programmes.extend(programmes)

        if programmes:
            channels_with_programmes += 1
            status = "built"
        else:
            channels_with_no_schedule += 1
            status = "no_schedule"
            warnings.append("selected schedule sources contained no programmes")

        unresolved = diagnostics["unresolved_baseline_conflicts"]
        same_start = diagnostics["same_start_conflicts"]
        ambiguous = sum(diagnostics["ambiguous_matches"].values())
        collisions = diagnostics["output_collision_count"]
        if unresolved:
            warnings.append(f"{unresolved} unresolved baseline conflicts")
        if same_start:
            warnings.append(f"{same_start} same-start baseline conflicts")
        if ambiguous:
            warnings.append(f"{ambiguous} ambiguous programme matches")
        if collisions:
            warnings.append(f"{collisions} programme collisions")

        total_baseline_duplicates += diagnostics[
            "baseline_duplicates_removed"
        ]
        channel_trims = diagnostics["baseline_boundary_trims"]
        total_boundary_trims += channel_trims
        total_baseline_fragments_dropped += diagnostics[
            "baseline_fragments_dropped"
        ]
        baseline_fragment_drop_details.extend(
            {"number": number, "name": name, **detail}
            for detail in diagnostics[
                "baseline_fragment_drop_details"
            ]
        )
        total_same_start_conflicts += same_start
        total_unresolved_conflicts += unresolved
        total_epgtalk_extensions += diagnostics[
            "epgtalk_extension_programmes_added"
        ]
        total_collisions += collisions
        standard_matches = sum(diagnostics["standard_matches"].values())
        padding_tolerant_matches = sum(
            diagnostics["padding_tolerant_matches"].values()
        )
        total_standard_matches += standard_matches
        total_padding_tolerant_matches += padding_tolerant_matches
        total_successful_enrichment_matches += (
            standard_matches + padding_tolerant_matches
        )
        total_ambiguous_relaxed_matches_rejected += sum(
            diagnostics["ambiguous_relaxed_matches_rejected"].values()
        )
        total_duration_mismatch_rejected += sum(
            diagnostics["duration_mismatch_rejected"].values()
        )
        total_start_mismatch_rejected += sum(
            diagnostics["start_mismatch_rejected"].values()
        )
        total_low_overlap_rejected += sum(
            diagnostics["low_overlap_rejected"].values()
        )
        total_metadata_conflict_rejected += sum(
            diagnostics["metadata_conflict_rejected"].values()
        )

        if channel_trims:
            channel_trim_counts.append(
                {"number": number, "name": name, "trims": channel_trims}
            )
        for detail in diagnostics["baseline_boundary_trim_details"]:
            trimmed_seconds = detail["trimmed_seconds"]
            distribution_key = str(trimmed_seconds)
            trim_duration_distribution[distribution_key] = (
                trim_duration_distribution.get(distribution_key, 0) + 1
            )
            if (
                detail["adjusted_duration_seconds"]
                < implausibly_short_threshold_seconds
            ):
                implausibly_short_programmes.append(
                    {
                        "number": number,
                        "name": name,
                        **detail,
                    }
                )

        per_channel_diagnostics.append(
            {
                "number": number,
                "name": name,
                "status": status,
                "programme_count": len(programmes),
                "warnings": warnings,
                "stale_mappings": stale_for_channel,
                "merge": diagnostics,
            }
        )

    try:
        resolver = apply_cached_episode_resolution(programme_groups)
    except Exception as error:
        resolver = {
            "provider": "tvmaze",
            "status": "resolver_unavailable",
            "error": f"resolver unavailable ({type(error).__name__})",
            "external_api_calls": 0,
            "candidate_records": 0,
            "confirmed_bindings": 0,
            "resolved_records": 0,
            "conflict_records": 0,
            "no_binding_records": 0,
        }

    if total_same_start_conflicts:
        parse_validation = {
            "valid": False,
            "error": (
                "build stopped before output: same-start baseline conflicts"
            ),
        }
    else:
        parse_validation = _write_combined_xmltv(
            output_channels, programme_groups, FULL_OUTPUT_XMLTV
        )

    channel_trim_counts.sort(
        key=lambda item: (-item["trims"], int(item["number"]))
    )
    max_trim_seconds = max(
        (int(seconds) for seconds in trim_duration_distribution),
        default=0,
    )

    starts = [
        (programme.get("start"), _timestamp(programme.get("start", "")))
        for programme in all_programmes
    ]
    stops = [
        (programme.get("stop"), _timestamp(programme.get("stop", "")))
        for programme in all_programmes
    ]

    return {
        "total_matrix_channels": len(rows),
        "channels_built_with_programmes": channels_with_programmes,
        "channels_with_no_schedule": channels_with_no_schedule,
        "stale_baseline_count": stale_report["stale_baseline_count"],
        "stale_baseline_mappings": (
            stale_report["stale_baseline_mappings"]
        ),
        "stale_epgshare_count": stale_report["stale_epgshare_count"],
        "stale_epgtalk_count": stale_report["stale_epgtalk_count"],
        "stale_epgshare_mappings": (
            stale_report["stale_epgshare_mappings"]
        ),
        "stale_epgtalk_mappings": (
            stale_report["stale_epgtalk_mappings"]
        ),
        "channels_degraded_to_baseline_only": (
            channels_degraded_to_baseline_only
        ),
        "total_output_programmes": len(all_programmes),
        "total_baseline_duplicates_removed": total_baseline_duplicates,
        "total_boundary_trims": total_boundary_trims,
        "total_generalized_boundary_trims": total_boundary_trims,
        "total_baseline_fragments_dropped": (
            total_baseline_fragments_dropped
        ),
        "baseline_fragment_drop_details": (
            baseline_fragment_drop_details
        ),
        "boundary_trim_duration_seconds_distribution": (
            trim_duration_distribution
        ),
        "max_boundary_trim_seconds": max_trim_seconds,
        "channels_with_most_boundary_trims": channel_trim_counts[:10],
        "implausibly_short_threshold_seconds": (
            implausibly_short_threshold_seconds
        ),
        "implausibly_short_programmes": implausibly_short_programmes,
        "total_same_start_conflicts": total_same_start_conflicts,
        "total_unresolved_baseline_conflicts": total_unresolved_conflicts,
        "total_epgtalk_extensions": total_epgtalk_extensions,
        "total_collisions": total_collisions,
        "total_successful_enrichment_matches": (
            total_successful_enrichment_matches
        ),
        "total_standard_matches": total_standard_matches,
        "total_padding_tolerant_matches": total_padding_tolerant_matches,
        "total_ambiguous_relaxed_matches_rejected": (
            total_ambiguous_relaxed_matches_rejected
        ),
        "total_duration_mismatch_rejected": (
            total_duration_mismatch_rejected
        ),
        "total_start_mismatch_rejected": total_start_mismatch_rejected,
        "total_low_overlap_rejected": total_low_overlap_rejected,
        "total_metadata_conflict_rejected": (
            total_metadata_conflict_rejected
        ),
        "episode_resolver": resolver,
        "output_written": total_same_start_conflicts == 0,
        "parse_validation": parse_validation,
        "output_first_programme_start": (
            min(starts, key=lambda item: item[1])[0] if starts else None
        ),
        "output_final_programme_stop": (
            max(stops, key=lambda item: item[1])[0] if stops else None
        ),
        "output_path": str(FULL_OUTPUT_XMLTV),
        "per_channel_diagnostics": per_channel_diagnostics,
    }
