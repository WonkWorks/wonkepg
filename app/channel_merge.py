"""Reusable single-channel XMLTV merge engine."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import xml.etree.ElementTree as ET


HIVE_XMLTV = Path("/data/hive.xml")
EPGSHARE_XMLTV = Path("/data/epgshare.xml")
EPGTALK_XMLTV = Path("/data/epgtalk.xml")
EPGTALK_LOCAL_XMLTV = Path("/data/epgtalk_local.xml")
MATCH_TOLERANCE_SECONDS = 120
PADDING_TOLERANT_DURATION_SECONDS = 180
PADDING_TOLERANT_MIN_OVERLAP = 0.90
PADDING_TOLERANT_DESCRIPTION_MIN_LENGTH = 80
PADDING_TOLERANT_DESCRIPTION_MIN_TOKEN_OVERLAP = 0.50

_XMLTV_TIME = re.compile(
    r"^(?P<stamp>\d{8}(?:\d{2}){0,3})(?:\s+(?P<offset>[+-]\d{4}))?"
)
_TITLE_DECORATION_PREFIXES = (
    ("new", re.compile(r"^\[new\]\s*:?\s*", re.IGNORECASE)),
    ("live", re.compile(r"^\[live\]\s*:?\s*", re.IGNORECASE)),
    ("new", re.compile(r"^new\s*:\s*", re.IGNORECASE)),
    ("live", re.compile(r"^live\s*:\s*", re.IGNORECASE)),
)
_TRAILING_NEW_DECORATION = re.compile(
    r"\s*\[new\]\s*:?\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class TitleSignals:
    new: bool = False
    live: bool = False


@dataclass(frozen=True)
class NormalizedTitle:
    canonical_title: str
    signals: TitleSignals


def normalize_title_decorations(value: str) -> NormalizedTitle:
    """Remove explicit title decorations and retain their source signals."""
    canonical = (value or "").strip()
    signals: set[str] = set()

    # Every successful pass shortens canonical. Refusing to strip a token that
    # would empty the title also protects output from decoration-only input.
    while canonical:
        changed = False
        for signal, pattern in _TITLE_DECORATION_PREFIXES:
            match = pattern.match(canonical)
            if match is None:
                continue
            remainder = canonical[match.end():].strip()
            if not remainder:
                continue
            canonical = remainder
            signals.add(signal)
            changed = True
            break
        if changed:
            continue

        match = _TRAILING_NEW_DECORATION.search(canonical)
        if match is not None:
            remainder = canonical[:match.start()].strip()
            if remainder:
                canonical = remainder
                signals.add("new")
                changed = True
        if not changed:
            break

    return NormalizedTitle(
        canonical_title=canonical,
        signals=TitleSignals(new="new" in signals, live="live" in signals),
    )


@dataclass
class Programme:
    element: ET.Element
    start: datetime
    stop: datetime
    title: str
    normalized_title: str
    title_signals: TitleSignals

    @property
    def duration_seconds(self) -> float:
        return (self.stop - self.start).total_seconds()


@dataclass
class ChannelMergeResult:
    channel: ET.Element
    programmes: list[ET.Element]
    diagnostics: dict


def _parse_xmltv_time(value: str) -> datetime:
    match = _XMLTV_TIME.match(value or "")
    if not match:
        raise ValueError(f"Unsupported XMLTV timestamp: {value!r}")

    stamp = match.group("stamp")
    formats = {
        8: "%Y%m%d",
        10: "%Y%m%d%H",
        12: "%Y%m%d%H%M",
        14: "%Y%m%d%H%M%S",
    }
    if len(stamp) not in formats:
        raise ValueError(f"Unsupported XMLTV timestamp precision: {value!r}")

    parsed = datetime.strptime(stamp, formats[len(stamp)])
    offset = match.group("offset")
    if offset:
        parsed = datetime.strptime(stamp + offset, formats[len(stamp)] + "%z")
    else:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_title(value: str) -> str:
    return normalize_title_decorations(value).canonical_title


def _normalized_title(value: str) -> str:
    return re.sub(r"\s+", " ", _clean_title(value)).casefold()


def _programme(element: ET.Element) -> Programme:
    title = element.findtext("title") or ""
    normalized = normalize_title_decorations(title)
    return Programme(
        element=element,
        start=_parse_xmltv_time(element.get("start", "")),
        stop=_parse_xmltv_time(element.get("stop", "")),
        title=title,
        normalized_title=_normalized_title(title),
        title_signals=normalized.signals,
    )


def load_selected_programmes(
    path: str | Path, channel_ids: set[str]
) -> dict[str, list[ET.Element]]:
    """Load selected XMLTV channels in one pass through a source file."""
    selected = {channel_id: [] for channel_id in channel_ids}
    if not selected:
        return selected

    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != "programme":
            continue
        channel_id = element.get("channel")
        if channel_id in selected:
            selected[channel_id].append(deepcopy(element))
        element.clear()
    return selected


def _prepared_programmes(elements: list[ET.Element]) -> list[Programme]:
    return [_programme(deepcopy(element)) for element in elements]


def _load_channel_programmes(path: Path, channel_id: str) -> list[Programme]:
    selected = load_selected_programmes(path, {channel_id})
    return [_programme(element) for element in selected[channel_id]]


def _is_conservative_match(left: Programme, right: Programme) -> bool:
    return (
        bool(left.normalized_title)
        and left.normalized_title == right.normalized_title
        and abs((left.start - right.start).total_seconds())
        <= MATCH_TOLERANCE_SECONDS
        and abs(left.duration_seconds - right.duration_seconds)
        <= MATCH_TOLERANCE_SECONDS
    )


def _interval_overlap_ratio(left: Programme, right: Programme) -> float:
    overlap_seconds = max(
        0.0,
        (min(left.stop, right.stop) - max(left.start, right.start))
        .total_seconds(),
    )
    longest_duration = max(left.duration_seconds, right.duration_seconds)
    return overlap_seconds / longest_duration if longest_duration > 0 else 0.0


def _metadata_identity_conflicts(
    left: Programme, right: Programme
) -> bool:
    left_subtitle = _normalized_title(left.element.findtext("sub-title") or "")
    right_subtitle = _normalized_title(
        right.element.findtext("sub-title") or ""
    )
    if left_subtitle and right_subtitle and left_subtitle != right_subtitle:
        return True

    left_ids = {
        element.get("system"): (element.text or "").strip().casefold()
        for element in left.element.findall("episode-num")
        if element.get("system") and (element.text or "").strip()
    }
    right_ids = {
        element.get("system"): (element.text or "").strip().casefold()
        for element in right.element.findall("episode-num")
        if element.get("system") and (element.text or "").strip()
    }
    for system in left_ids.keys() & right_ids.keys():
        if left_ids[system] != right_ids[system]:
            return True

    left_description = (left.element.findtext("desc") or "").strip()
    right_description = (right.element.findtext("desc") or "").strip()
    if (
        len(left_description) < PADDING_TOLERANT_DESCRIPTION_MIN_LENGTH
        or len(right_description) < PADDING_TOLERANT_DESCRIPTION_MIN_LENGTH
    ):
        return False
    left_tokens = {
        token
        for token in re.findall(r"[a-z0-9']+", left_description.casefold())
        if len(token) > 3
    }
    right_tokens = {
        token
        for token in re.findall(r"[a-z0-9']+", right_description.casefold())
        if len(token) > 3
    }
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens) / min(
        len(left_tokens), len(right_tokens)
    )
    return overlap < PADDING_TOLERANT_DESCRIPTION_MIN_TOKEN_OVERLAP


def _match_kind(left: Programme, right: Programme) -> str | None:
    """Classify a title/timing match without changing baseline timing."""
    if (
        not left.normalized_title
        or left.normalized_title != right.normalized_title
    ):
        return None
    if (
        abs((left.start - right.start).total_seconds())
        > MATCH_TOLERANCE_SECONDS
    ):
        return None
    duration_delta = abs(left.duration_seconds - right.duration_seconds)
    if duration_delta <= MATCH_TOLERANCE_SECONDS:
        return "standard"
    if (
        duration_delta <= PADDING_TOLERANT_DURATION_SECONDS
        and _interval_overlap_ratio(left, right)
        >= PADDING_TOLERANT_MIN_OVERLAP
        and not _metadata_identity_conflicts(left, right)
    ):
        return "padding_tolerant"
    return None


def _normalize_baseline(
    programmes: list[Programme],
) -> tuple[list[Programme], list[dict]]:
    retained: list[Programme] = []
    removed: list[dict] = []

    for candidate in sorted(programmes, key=lambda item: (item.start, item.stop)):
        duplicate = next(
            (
                existing
                for existing in reversed(retained)
                if abs((candidate.start - existing.start).total_seconds())
                <= MATCH_TOLERANCE_SECONDS
                and _is_conservative_match(existing, candidate)
            ),
            None,
        )
        if duplicate is None:
            retained.append(candidate)
            continue

        removed.append(
            {
                "title": _clean_title(candidate.title),
                "retained_start": duplicate.element.get("start"),
                "discarded_start": candidate.element.get("start"),
            }
        )

    return retained, removed


def _normalize_baseline_boundaries(
    programmes: list[Programme],
) -> tuple[list[dict], list[dict], list[dict]]:
    trims: list[dict] = []
    same_start_conflicts: list[dict] = []
    fragments_dropped: list[dict] = []
    programmes.sort(key=lambda item: (item.start, item.stop))

    index = 0
    while index < len(programmes) - 1:
        earlier = programmes[index]
        later = programmes[index + 1]
        if earlier.start == later.start:
            same_start_conflicts.append(
                {
                    "start": earlier.element.get("start"),
                    "earlier_title": _clean_title(earlier.title),
                    "earlier_stop": earlier.element.get("stop"),
                    "later_title": _clean_title(later.title),
                    "later_stop": later.element.get("stop"),
                }
            )
            index += 1
            continue
        if not (earlier.start < later.start < earlier.stop):
            index += 1
            continue

        original_stop = earlier.element.get("stop")
        adjusted_stop = later.element.get("start", "")
        trimmed_seconds = int((earlier.stop - later.start).total_seconds())
        adjusted_duration_seconds = int(
            (later.start - earlier.start).total_seconds()
        )
        if adjusted_duration_seconds < 300:
            fragments_dropped.append(
                {
                    "type": "baseline_fragment_dropped",
                    "channel": earlier.element.get("channel"),
                    "title": _clean_title(earlier.title),
                    "original_start": earlier.element.get("start"),
                    "original_stop": original_stop,
                    "would_be_stop": adjusted_stop,
                    "would_be_duration_seconds": adjusted_duration_seconds,
                    "trimmed_seconds": trimmed_seconds,
                    "following_programme": {
                        "channel": later.element.get("channel"),
                        "title": _clean_title(later.title),
                        "start": later.element.get("start"),
                        "stop": later.element.get("stop"),
                    },
                }
            )
            del programmes[index]
            continue

        earlier.element.set("stop", adjusted_stop)
        earlier.stop = later.start
        trims.append(
            {
                "type": "baseline_boundary_trim",
                "earlier_title": _clean_title(earlier.title),
                "earlier_start": earlier.element.get("start"),
                "later_title": _clean_title(later.title),
                "later_start": later.element.get("start"),
                "original_stop": original_stop,
                "adjusted_stop": adjusted_stop,
                "trimmed_seconds": trimmed_seconds,
                "adjusted_duration_seconds": adjusted_duration_seconds,
                "earlier_original_stop": original_stop,
                "earlier_trimmed_stop": adjusted_stop,
                "overlap_seconds": trimmed_seconds,
            }
        )
        index += 1

    return trims, same_start_conflicts, fragments_dropped


def _candidate_edges(
    baseline: list[Programme], enrichment: list[Programme]
) -> tuple[list[list[tuple[int, str]]], list[list[tuple[int, str]]]]:
    by_enrichment: list[list[tuple[int, str]]] = []
    by_baseline: list[list[tuple[int, str]]] = [[] for _ in baseline]
    for enrichment_index, candidate in enumerate(enrichment):
        matches = []
        for baseline_index, canonical in enumerate(baseline):
            kind = _match_kind(canonical, candidate)
            if kind is None:
                continue
            matches.append((baseline_index, kind))
            by_baseline[baseline_index].append((enrichment_index, kind))
        by_enrichment.append(matches)
    return by_enrichment, by_baseline


def _unmatched_reason(
    baseline: list[Programme], candidate: Programme
) -> str | None:
    same_title = [
        canonical
        for canonical in baseline
        if canonical.normalized_title
        and canonical.normalized_title == candidate.normalized_title
    ]
    if not same_title:
        return None
    near_start = [
        canonical
        for canonical in same_title
        if abs((canonical.start - candidate.start).total_seconds())
        <= MATCH_TOLERANCE_SECONDS
    ]
    if not near_start:
        return "start_mismatch"
    bounded_duration = [
        canonical
        for canonical in near_start
        if abs(canonical.duration_seconds - candidate.duration_seconds)
        <= PADDING_TOLERANT_DURATION_SECONDS
    ]
    if not bounded_duration:
        return "duration_mismatch"
    high_overlap = [
        canonical
        for canonical in bounded_duration
        if _interval_overlap_ratio(canonical, candidate)
        >= PADDING_TOLERANT_MIN_OVERLAP
    ]
    if not high_overlap:
        return "low_overlap"
    if all(
        _metadata_identity_conflicts(canonical, candidate)
        for canonical in high_overlap
    ):
        return "metadata_conflict"
    return None


def _match_source(
    baseline: list[Programme], enrichment: list[Programme]
) -> tuple[dict[int, Programme], dict]:
    by_enrichment, by_baseline = _candidate_edges(baseline, enrichment)
    matches: dict[int, Programme] = {}
    counts = {
        "exact": 0,
        "near": 0,
        "ambiguous": 0,
        "standard": 0,
        "padding_tolerant": 0,
        "ambiguous_relaxed": 0,
        "duration_mismatch_rejected": 0,
        "start_mismatch_rejected": 0,
        "low_overlap_rejected": 0,
        "metadata_conflict_rejected": 0,
    }
    unmatched_indices: list[int] = []

    for enrichment_index, edges in enumerate(by_enrichment):
        if not edges:
            unmatched_indices.append(enrichment_index)
            reason = _unmatched_reason(
                baseline, enrichment[enrichment_index]
            )
            if reason is not None:
                counts[f"{reason}_rejected"] += 1
            continue

        if len(edges) != 1:
            counts["ambiguous"] += 1
            if any(kind == "padding_tolerant" for _, kind in edges):
                counts["ambiguous_relaxed"] += 1
            continue

        baseline_index, kind = edges[0]
        if len(by_baseline[baseline_index]) != 1:
            counts["ambiguous"] += 1
            if (
                kind == "padding_tolerant"
                or any(
                    shared_kind == "padding_tolerant"
                    for _, shared_kind in by_baseline[baseline_index]
                )
            ):
                counts["ambiguous_relaxed"] += 1
            continue

        candidate = enrichment[enrichment_index]
        canonical = baseline[baseline_index]
        matches[baseline_index] = candidate
        counts[kind] += 1
        if candidate.start == canonical.start and candidate.stop == canonical.stop:
            counts["exact"] += 1
        else:
            counts["near"] += 1

    return matches, {**counts, "unmatched_indices": unmatched_indices}


def _element_text_key(element: ET.Element) -> tuple:
    return (
        element.tag,
        tuple(sorted(element.attrib.items())),
        (element.text or "").strip().casefold(),
    )


def _child_key(element: ET.Element) -> tuple:
    if element.tag == "rating":
        return (
            element.tag,
            element.get("system", "").casefold(),
            tuple(
                (value.text or "").strip().casefold()
                for value in element.findall("value")
            ),
        )
    if element.tag in ("video", "audio"):
        return (element.tag, ET.tostring(element, encoding="unicode"))
    return _element_text_key(element)


def _valid_date(value: str) -> bool:
    if not value or not value.isdigit() or len(value) not in (4, 6, 8):
        return False
    try:
        datetime.strptime(value, {4: "%Y", 6: "%Y%m", 8: "%Y%m%d"}[len(value)])
    except ValueError:
        return False
    return True


def _replace_single(parent: ET.Element, tag: str, replacement: ET.Element) -> None:
    for existing in list(parent.findall(tag)):
        parent.remove(existing)
    parent.append(deepcopy(replacement))


def _merge_credits(target: ET.Element, candidate: ET.Element) -> int:
    source_credits = candidate.find("credits")
    if source_credits is None:
        return 0

    target_credits = target.find("credits")
    if target_credits is None:
        target_credits = ET.Element("credits")
        target.append(target_credits)

    existing = {
        (credit.tag, (credit.text or "").strip().casefold())
        for credit in target_credits
    }
    added = 0
    for credit in source_credits:
        key = (credit.tag, (credit.text or "").strip().casefold())
        if not key[1] or key in existing:
            continue
        target_credits.append(deepcopy(credit))
        existing.add(key)
        added += 1
    return added


def _merge_repeated(target: ET.Element, candidate: ET.Element, tag: str) -> int:
    existing = {_child_key(element) for element in target.findall(tag)}
    added = 0
    for element in candidate.findall(tag):
        key = _child_key(element)
        if key in existing or (
            tag == "episode-num" and not (element.text or "").strip()
        ):
            continue
        target.append(deepcopy(element))
        existing.add(key)
        added += 1
    return added


def _ensure_single_marker(
    parent: ET.Element, tag: str, required: bool
) -> None:
    markers = parent.findall(tag)
    for duplicate in markers[1:]:
        parent.remove(duplicate)
    if required and not markers:
        parent.append(ET.Element(tag))


def _merge_title_markers(target: ET.Element, candidate: Programme) -> None:
    _ensure_single_marker(
        target,
        "new",
        target.find("new") is not None
        or candidate.element.find("new") is not None
        or candidate.title_signals.new,
    )
    _ensure_single_marker(
        target,
        "live",
        target.find("live") is not None
        or candidate.element.find("live") is not None
        or candidate.title_signals.live,
    )


def _merge_candidate(
    target: ET.Element, candidate: Programme
) -> dict:
    additions = {
        "icons": 0,
        "episode_nums": 0,
        "categories": 0,
        "credits": 0,
    }

    candidate_subtitle = candidate.element.find("sub-title")
    if target.find("sub-title") is None and candidate_subtitle is not None:
        target.append(deepcopy(candidate_subtitle))

    candidate_desc = candidate.element.find("desc")
    target_desc = target.find("desc")
    target_desc_text = (
        (target_desc.text or "").strip() if target_desc is not None else ""
    )
    if (
        candidate_desc is not None
        and len((candidate_desc.text or "").strip()) > len(target_desc_text)
    ):
        _replace_single(target, "desc", candidate_desc)

    additions["credits"] += _merge_credits(target, candidate.element)
    additions["categories"] += _merge_repeated(
        target, candidate.element, "category"
    )
    additions["icons"] += _merge_repeated(target, candidate.element, "icon")
    additions["episode_nums"] += _merge_repeated(
        target, candidate.element, "episode-num"
    )
    _merge_repeated(target, candidate.element, "rating")
    _merge_repeated(target, candidate.element, "subtitles")

    candidate_date = candidate.element.find("date")
    target_date = target.find("date")
    candidate_date_text = (
        (candidate_date.text or "").strip() if candidate_date is not None else ""
    )
    target_date_text = (
        (target_date.text or "").strip() if target_date is not None else ""
    )
    if _valid_date(candidate_date_text) and (
        not _valid_date(target_date_text)
        or len(candidate_date_text) > len(target_date_text)
    ):
        _replace_single(target, "date", candidate_date)

    candidate_previous = candidate.element.find("previously-shown")
    target_previous = target.find("previously-shown")
    if candidate_previous is not None and (
        target_previous is None
        or (
            candidate_previous.get("start")
            and not target_previous.get("start")
        )
        or len(candidate_previous.attrib) > len(target_previous.attrib)
    ):
        _replace_single(target, "previously-shown", candidate_previous)

    for tag in ("length", "video", "audio"):
        source_element = candidate.element.find(tag)
        if target.find(tag) is None and source_element is not None:
            target.append(deepcopy(source_element))

    _merge_title_markers(target, candidate)

    return additions


_PROGRAMME_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "title",
            "sub-title",
            "desc",
            "credits",
            "date",
            "category",
            "keyword",
            "language",
            "orig-language",
            "length",
            "icon",
            "url",
            "country",
            "episode-num",
            "video",
            "audio",
            "previously-shown",
            "premiere",
            "last-chance",
            "new",
            "live",
            "subtitles",
            "rating",
            "star-rating",
            "review",
        )
    )
}

_CREDIT_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "director",
            "actor",
            "writer",
            "adapter",
            "producer",
            "composer",
            "editor",
            "presenter",
            "commentator",
            "guest",
        )
    )
}


def create_output_channel(channel_row: dict) -> ET.Element:
    """Create the XMLTV channel definition for one persisted matrix row."""
    if not channel_row.get("number") or not channel_row.get("name"):
        raise ValueError("Channel row must contain number and name")
    pretty_name = channel_row.get("pretty_name", channel_row["name"])
    if not isinstance(pretty_name, str) or len(pretty_name) > 50:
        raise ValueError("Pretty Name must be a string of at most 50 characters")

    channel = ET.Element("channel", {"id": f"wonk.{channel_row['number']}"})
    ET.SubElement(channel, "display-name").text = pretty_name
    ET.SubElement(channel, "display-name").text = channel_row["name"]
    if channel_row.get("logo"):
        ET.SubElement(channel, "icon", {"src": channel_row["logo"]})
    return channel


def _normalize_output_element(
    source: Programme, output_channel_id: str
) -> ET.Element:
    output = deepcopy(source.element)
    output.attrib.clear()
    output.set("start", source.element.get("start", ""))
    output.set("stop", source.element.get("stop", ""))
    output.set("channel", output_channel_id)
    title = output.find("title")
    if title is not None:
        title.text = _clean_title(title.text or "")
    _ensure_single_marker(
        output,
        "new",
        output.find("new") is not None or source.title_signals.new,
    )
    _ensure_single_marker(
        output,
        "live",
        output.find("live") is not None or source.title_signals.live,
    )
    return output


def _sort_programme_children(programme: ET.Element) -> None:
    credits = programme.find("credits")
    if credits is not None:
        credits[:] = sorted(
            credits, key=lambda item: _CREDIT_ORDER.get(item.tag, 999)
        )
    programme[:] = sorted(
        programme,
        key=lambda item: _PROGRAMME_ORDER.get(item.tag, 999),
    )


def _overlaps(left: Programme, right: Programme) -> bool:
    return left.start < right.stop and right.start < left.stop


def _collision_count(programmes: list[Programme]) -> int:
    ordered = sorted(programmes, key=lambda item: (item.start, item.stop))
    return sum(
        _overlaps(left, right)
        for left, right in zip(ordered, ordered[1:])
    )


def merge_single_channel(
    channel_row: dict,
    baseline_xmltv: str | Path | None,
    baseline_channel_id: str | None,
    baseline_programmes: list[ET.Element] | None = None,
    epgshare_channel_id: str | None = None,
    epgtalk_channel_id: str | None = None,
    epgshare_xmltv: str | Path = EPGSHARE_XMLTV,
    epgtalk_xmltv: str | Path = EPGTALK_XMLTV,
    epgshare_programmes: list[ET.Element] | None = None,
    epgtalk_programmes: list[ET.Element] | None = None,
) -> ChannelMergeResult:
    """Merge one persisted Wonk channel row into an XMLTV channel result."""
    if not channel_row.get("number") or not channel_row.get("name"):
        raise ValueError("Channel row must contain number and name")

    output_channel_id = f"wonk.{channel_row['number']}"
    baseline_source = (
        _prepared_programmes(baseline_programmes)
        if baseline_programmes is not None
        else _load_channel_programmes(
            Path(baseline_xmltv), baseline_channel_id
        )
        if baseline_channel_id and baseline_xmltv is not None
        else []
    )
    epgshare_source = (
        (
            _prepared_programmes(epgshare_programmes)
            if epgshare_programmes is not None
            else _load_channel_programmes(
                Path(epgshare_xmltv), epgshare_channel_id
            )
        )
        if epgshare_channel_id is not None
        else []
    )
    epgtalk_source = (
        (
            _prepared_programmes(epgtalk_programmes)
            if epgtalk_programmes is not None
            else _load_channel_programmes(
                Path(epgtalk_xmltv), epgtalk_channel_id
            )
        )
        if epgtalk_channel_id is not None
        else []
    )

    baseline, baseline_duplicates = _normalize_baseline(baseline_source)
    (
        baseline_boundary_trims,
        same_start_conflicts,
        baseline_fragments_dropped,
    ) = _normalize_baseline_boundaries(baseline)
    _, epgtalk_schedule_diagnostics = _match_source(
        baseline, epgtalk_source
    )
    extension_programmes: list[Programme] = []
    canonical_schedule = list(baseline)
    for unmatched_index in epgtalk_schedule_diagnostics["unmatched_indices"]:
        candidate = epgtalk_source[unmatched_index]
        if any(
            _overlaps(candidate, existing)
            for existing in canonical_schedule
        ):
            continue
        extension_programmes.append(candidate)
        canonical_schedule.append(candidate)
    canonical_schedule.sort(key=lambda item: (item.start, item.stop))

    epgshare_matches, epgshare_diagnostics = _match_source(
        canonical_schedule, epgshare_source
    )
    epgtalk_matches, epgtalk_diagnostics = _match_source(
        canonical_schedule, epgtalk_source
    )

    enrichment_totals = {
        "icons_added": 0,
        "episode_num_values_added": 0,
        "categories_added": 0,
        "credits_added": 0,
    }
    output_programmes: list[Programme] = []

    for canonical_index, canonical in enumerate(canonical_schedule):
        output_element = _normalize_output_element(
            canonical, output_channel_id
        )
        for source_name, matches in (
            ("epgshare", epgshare_matches),
            ("epgtalk", epgtalk_matches),
        ):
            candidate = matches.get(canonical_index)
            if candidate is None:
                continue
            additions = _merge_candidate(output_element, candidate)
            enrichment_totals["icons_added"] += additions["icons"]
            enrichment_totals["episode_num_values_added"] += additions[
                "episode_nums"
            ]
            enrichment_totals["categories_added"] += additions["categories"]
            enrichment_totals["credits_added"] += additions["credits"]
        _sort_programme_children(output_element)
        output_programmes.append(_programme(output_element))

    output_programmes.sort(key=lambda item: (item.start, item.stop))

    channel = create_output_channel(channel_row)

    collisions = _collision_count(output_programmes)
    chronologically_sorted = all(
        left.start <= right.start
        for left, right in zip(output_programmes, output_programmes[1:])
    )

    diagnostics = {
        "channel": output_channel_id,
        "baseline_source_programme_count": len(baseline_source),
        "hive_source_programme_count": len(baseline_source),
        "epgshare_source_programme_count": len(epgshare_source),
        "epgtalk_source_programme_count": len(epgtalk_source),
        "normalized_baseline_programme_count": len(baseline),
        "normalized_hive_programme_count": len(baseline),
        "baseline_duplicates_removed": len(baseline_duplicates),
        "baseline_duplicate_details": baseline_duplicates,
        "baseline_boundary_trims": len(baseline_boundary_trims),
        "baseline_boundary_trim_details": baseline_boundary_trims,
        "baseline_fragments_dropped": len(baseline_fragments_dropped),
        "baseline_fragment_drop_details": baseline_fragments_dropped,
        "same_start_conflicts": len(same_start_conflicts),
        "same_start_conflict_details": same_start_conflicts,
        "unresolved_baseline_conflicts": 0,
        "unresolved_baseline_conflict_details": [],
        "canonical_programme_count_before_enrichment": len(
            canonical_schedule
        ),
        "epgtalk_schedule_pass": {
            "exact": epgtalk_schedule_diagnostics["exact"],
            "near": epgtalk_schedule_diagnostics["near"],
            "unmatched": len(epgtalk_schedule_diagnostics["unmatched_indices"]),
            "ambiguous": epgtalk_schedule_diagnostics["ambiguous"],
        },
        "exact_matches": {
            "epgshare": epgshare_diagnostics["exact"],
            "epgtalk": epgtalk_diagnostics["exact"],
        },
        "near_matches": {
            "epgshare": epgshare_diagnostics["near"],
            "epgtalk": epgtalk_diagnostics["near"],
        },
        "standard_matches": {
            "epgshare": epgshare_diagnostics["standard"],
            "epgtalk": epgtalk_diagnostics["standard"],
        },
        "padding_tolerant_matches": {
            "epgshare": epgshare_diagnostics["padding_tolerant"],
            "epgtalk": epgtalk_diagnostics["padding_tolerant"],
        },
        "unmatched_enrichment_programmes": {
            "epgshare": len(epgshare_diagnostics["unmatched_indices"]),
            "epgtalk": len(epgtalk_diagnostics["unmatched_indices"]),
        },
        "ambiguous_matches": {
            "epgshare": epgshare_diagnostics["ambiguous"],
            "epgtalk": epgtalk_diagnostics["ambiguous"],
        },
        "ambiguous_relaxed_matches_rejected": {
            "epgshare": epgshare_diagnostics["ambiguous_relaxed"],
            "epgtalk": epgtalk_diagnostics["ambiguous_relaxed"],
        },
        "duration_mismatch_rejected": {
            "epgshare": epgshare_diagnostics["duration_mismatch_rejected"],
            "epgtalk": epgtalk_diagnostics["duration_mismatch_rejected"],
        },
        "start_mismatch_rejected": {
            "epgshare": epgshare_diagnostics["start_mismatch_rejected"],
            "epgtalk": epgtalk_diagnostics["start_mismatch_rejected"],
        },
        "low_overlap_rejected": {
            "epgshare": epgshare_diagnostics["low_overlap_rejected"],
            "epgtalk": epgtalk_diagnostics["low_overlap_rejected"],
        },
        "metadata_conflict_rejected": {
            "epgshare": epgshare_diagnostics["metadata_conflict_rejected"],
            "epgtalk": epgtalk_diagnostics["metadata_conflict_rejected"],
        },
        "epgtalk_extension_programmes_added": len(extension_programmes),
        "enrichment_totals": enrichment_totals,
        "output_programme_count": len(output_programmes),
        "first_output_start": (
            output_programmes[0].element.get("start")
            if output_programmes
            else None
        ),
        "final_output_stop": (
            output_programmes[-1].element.get("stop")
            if output_programmes
            else None
        ),
        "chronologically_sorted": chronologically_sorted,
        "output_collision_count": collisions,
    }
    return ChannelMergeResult(
        channel=channel,
        programmes=[programme.element for programme in output_programmes],
        diagnostics=diagnostics,
    )


def write_channel_xmltv(
    result: ChannelMergeResult, output_path: str | Path
) -> dict:
    """Write one channel merge result and verify that it parses as XML."""
    destination = Path(output_path)
    root = ET.Element(
        "tv", {"generator-info-name": "WonkEPG single-channel merge"}
    )
    root.append(deepcopy(result.channel))
    for programme in result.programmes:
        root.append(deepcopy(programme))

    destination.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(destination, encoding="utf-8", xml_declaration=True)

    parse_validation = {"valid": True, "error": None}
    try:
        ET.parse(destination)
    except (ET.ParseError, OSError) as error:
        parse_validation = {"valid": False, "error": str(error)}
    return parse_validation
