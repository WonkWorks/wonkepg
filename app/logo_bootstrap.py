"""Dry-run discovery for migrating persisted channel logos into WonkEPG."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


from app.location_safety import UnsafeLocationError, validate_http_location
from app.version import __version__
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_LOGO_BYTES = 20 * 1024 * 1024
SCAN_TTL = timedelta(minutes=30)
CATEGORIES = (
    "valid_png",
    "already_local",
    "no_logo",
    "jpeg",
    "svg",
    "webp",
    "unknown_non_png",
    "unreachable",
    "other_failure",
)


@dataclass(frozen=True)
class DownloadResult:
    category: str
    contents: bytes | None = None
    error: str | None = None


def _is_local_logo(url: str, public_base_url: str) -> bool:
    candidate = urlsplit(url)
    base = urlsplit(public_base_url)
    return (
        candidate.scheme.casefold() == base.scheme.casefold()
        and candidate.netloc.casefold() == base.netloc.casefold()
        and candidate.path.startswith(base.path.rstrip("/") + "/")
    )


def _origin(url: str) -> str:
    """Classify without embedding reference-installation host knowledge."""
    return "external"


def _classify_bytes(contents: bytes) -> str:
    if contents.startswith(PNG_SIGNATURE):
        return "valid_png"
    if contents.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if (
        len(contents) >= 12
        and contents.startswith(b"RIFF")
        and contents[8:12] == b"WEBP"
    ):
        return "webp"
    prefix = contents[:8192].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    if prefix.startswith(b"<svg") or (
        prefix.startswith(b"<?xml") and b"<svg" in prefix
    ):
        return "svg"
    return "unknown_non_png"


def download_and_classify(url: str) -> DownloadResult:
    """Download one persisted HTTP URL and classify its actual bytes."""
    try:
        url = validate_http_location(url)
    except UnsafeLocationError:
        return DownloadResult("unreachable", error="URL is not allowed")
    request = Request(
        url,
        headers={"User-Agent": f"WonkEPG/{__version__}", "Accept-Encoding": "identity"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                return DownloadResult(
                    "unreachable", error=f"HTTP status {status}"
                )
            contents = response.read(MAX_LOGO_BYTES + 1)
    except HTTPError as error:
        return DownloadResult(
            "unreachable", error=f"HTTP status {error.code}"
        )
    except (URLError, TimeoutError, OSError):
        return DownloadResult("unreachable", error="download failed")
    except Exception as error:
        return DownloadResult(
            "other_failure", error=f"download failed ({type(error).__name__})"
        )

    if len(contents) > MAX_LOGO_BYTES:
        return DownloadResult("other_failure", error="image exceeds 20 MiB")
    return DownloadResult(_classify_bytes(contents), contents=contents)


class LogoBootstrapStore:
    """Keep short-lived server-side scan manifests; never cache image bytes."""

    def __init__(self):
        self._lock = threading.Lock()
        self.apply_lock = threading.Lock()
        self._scans: dict[str, dict] = {}

    def _discard_expired(self, now: datetime) -> None:
        expired = [
            scan_id
            for scan_id, scan in self._scans.items()
            if now - scan["created_at"] > SCAN_TTL
        ]
        for scan_id in expired:
            del self._scans[scan_id]

    def scan(self, matrix: dict, public_base_url: str) -> dict:
        now = datetime.now(timezone.utc)
        results = []
        candidates = []

        for row in matrix.get("channels", []):
            number = str(row.get("number") or "")
            name = row.get("name") or ""
            logo = row.get("logo")
            item = {
                "number": number,
                "name": name,
                "logo": logo,
                "category": None,
                "reason": None,
            }
            if not logo:
                item.update(category="no_logo", reason="no persisted logo URL")
            elif not isinstance(logo, str):
                item.update(
                    category="other_failure", reason="logo URL is not text"
                )
            elif _is_local_logo(logo, public_base_url):
                item.update(
                    category="already_local",
                    reason="already uses WonkEPG logo hosting",
                )
            elif not number.isascii() or not number.isdecimal() or int(number) > 99999:
                item.update(
                    category="other_failure", reason="invalid channel number"
                )
            else:
                downloaded = download_and_classify(logo)
                item["category"] = downloaded.category
                item["reason"] = downloaded.error or {
                    "valid_png": "valid PNG candidate",
                    "jpeg": "JPEG is not migrated",
                    "svg": "SVG is not migrated",
                    "webp": "WebP is not migrated",
                    "unknown_non_png": "download is not a recognized PNG",
                }.get(downloaded.category)
                if downloaded.category == "valid_png":
                    digest = sha256(downloaded.contents).hexdigest()
                    item.update(
                        sha256=digest,
                        bytes=len(downloaded.contents),
                        origin=_origin(logo),
                    )
                    candidates.append(
                        {
                            "number": number,
                            "name": name,
                            "logo": logo,
                            "sha256": digest,
                        }
                    )
            results.append(item)

        counts = Counter(item["category"] for item in results)
        hash_groups = defaultdict(list)
        for item in results:
            if item["category"] == "valid_png":
                hash_groups[item["sha256"]].append(item["number"])
        duplicate_groups = [group for group in hash_groups.values() if len(group) > 1]
        scan_id = uuid4().hex
        with self._lock:
            self._discard_expired(now)
            self._scans[scan_id] = {
                "created_at": now,
                "candidates": candidates,
            }

        manual_attention = [
            item
            for item in results
            if item["category"] not in {"valid_png", "already_local"}
        ]
        return {
            "scan_id": scan_id,
            "created_at": now.isoformat(),
            "expires_at": (now + SCAN_TTL).isoformat(),
            "summary": {
                "total_channels": len(results),
                **{category: counts[category] for category in CATEGORIES},
                "configured_m3u_png_candidates": sum(
                    item.get("origin") == "threadfin_cache" for item in results
                ),
                "external_png_candidates": sum(
                    item.get("origin") == "external" for item in results
                ),
                "duplicate_pngs": sum(len(group) - 1 for group in duplicate_groups),
                "duplicate_png_groups": len(duplicate_groups),
                "manual_attention": len(manual_attention),
            },
            "duplicate_groups": duplicate_groups,
            "manual_attention": manual_attention,
        }

    def candidates(self, scan_id: str) -> list[dict] | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._discard_expired(now)
            scan = self._scans.get(scan_id)
            return list(scan["candidates"]) if scan else None


bootstrap_store = LogoBootstrapStore()
