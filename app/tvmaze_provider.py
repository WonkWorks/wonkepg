"""Bounded TVmaze access and cache handling for episode resolution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.version import __version__


MAX_RESPONSE_BYTES = 32 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 10


class ProviderError(RuntimeError):
    """A sanitized external-provider or cache failure."""


@dataclass(frozen=True)
class EpisodeCatalog:
    provider: str
    show_id: int
    canonical_name: str
    fetched_at: str
    episodes: tuple[dict, ...]


class EpisodeResolverProvider(ABC):
    """Small provider boundary; normal guide builds use cache methods only."""

    provider_id: str

    @abstractmethod
    def search_shows(self, query: str) -> list[dict]:
        """Perform an explicit onboarding search."""

    @abstractmethod
    def refresh_catalog(self, show_id: int, canonical_name: str) -> dict:
        """Refresh one show catalog without destroying last-known-good data."""

    @abstractmethod
    def get_cached_episode_catalog(self, show_id: int) -> EpisodeCatalog:
        """Read and validate one local show catalog without network access."""

    @abstractmethod
    def resolve_season_for_episode(
        self, catalog: EpisodeCatalog, airdate: str
    ) -> list[dict]:
        """Return provider episodes on one canonical original-air date."""


def _positive_show_id(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("show ID must be a positive integer")
    try:
        show_id = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("show ID must be a positive integer") from error
    if show_id < 1:
        raise ValueError("show ID must be a positive integer")
    return show_id


class TVmazeProvider(EpisodeResolverProvider):
    provider_id = "tvmaze"
    api_root = "https://api.tvmaze.com"

    def __init__(self, cache_root: str | Path):
        self.cache_root = Path(cache_root)

    def _request_json(self, url: str):
        headers = {
            "Accept": "application/json",
            "User-Agent": f"WonkEPG/{__version__} episode-resolver",
        }
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                request = Request(url, headers=headers)
                with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    length = response.headers.get("Content-Length")
                    if length and int(length) > MAX_RESPONSE_BYTES:
                        raise ProviderError("TVmaze response exceeds safety limit")
                    payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ProviderError("TVmaze response exceeds safety limit")
                return json.loads(payload)
            except HTTPError as error:
                last_error = error
                retryable = error.code == 429 or 500 <= error.code < 600
                if not retryable or attempt + 1 == MAX_ATTEMPTS:
                    break
                retry_after = error.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(min(max(delay, 0), MAX_BACKOFF_SECONDS))
            except (TimeoutError, URLError, json.JSONDecodeError, OSError) as error:
                last_error = error
                if attempt + 1 == MAX_ATTEMPTS:
                    break
                time.sleep(min(2 ** attempt, MAX_BACKOFF_SECONDS))
        kind = type(last_error).__name__ if last_error else "ProviderError"
        raise ProviderError(f"TVmaze request failed ({kind})")

    def search_shows(self, query: str) -> list[dict]:
        query = str(query or "").strip()
        if not query or len(query) > 200:
            raise ValueError("search query must contain 1 to 200 characters")
        payload = self._request_json(
            f"{self.api_root}/search/shows?q={quote(query)}"
        )
        if not isinstance(payload, list):
            raise ProviderError("TVmaze returned malformed search data")
        results = []
        for item in payload[:20]:
            show = item.get("show") if isinstance(item, dict) else None
            if not isinstance(show, dict):
                continue
            try:
                show_id = _positive_show_id(show.get("id"))
            except ValueError:
                continue
            network = show.get("network") or show.get("webChannel") or {}
            results.append({
                "provider": self.provider_id,
                "show_id": show_id,
                "canonical_name": str(show.get("name") or ""),
                "type": show.get("type"),
                "network": network.get("name") if isinstance(network, dict) else None,
                "premiered": show.get("premiered"),
                "status": show.get("status"),
                "score": item.get("score"),
                "url": show.get("url"),
            })
        return results

    def _cache_path(self, show_id: int) -> Path:
        return self.cache_root / f"show-{_positive_show_id(show_id)}.json"

    @staticmethod
    def _validated_episodes(payload: object) -> tuple[dict, ...]:
        if not isinstance(payload, list):
            raise ProviderError("TVmaze returned malformed episode data")
        episodes = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                raise ProviderError("TVmaze returned malformed episode data")
            episodes.append({
                "id": item["id"],
                "name": item.get("name"),
                "season": item.get("season"),
                "number": item.get("number"),
                "type": item.get("type"),
                "airdate": item.get("airdate"),
                "airtime": item.get("airtime"),
                "runtime": item.get("runtime"),
            })
        return tuple(episodes)

    def refresh_catalog(self, show_id: int, canonical_name: str) -> dict:
        show_id = _positive_show_id(show_id)
        payload = self._request_json(
            f"{self.api_root}/shows/{show_id}/episodes?specials=1"
        )
        episodes = self._validated_episodes(payload)
        document = {
            "schema_version": 1,
            "provider": self.provider_id,
            "show_id": show_id,
            "canonical_name": str(canonical_name or ""),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "episodes": list(episodes),
        }
        self.cache_root.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(show_id)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".show-{show_id}.", suffix=".tmp", dir=self.cache_root
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(document, output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
            directory = os.open(self.cache_root, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return {
            "provider": self.provider_id,
            "show_id": show_id,
            "episodes": len(episodes),
            "fetched_at": document["fetched_at"],
        }

    def resolve_season_for_episode(
        self, catalog: EpisodeCatalog, airdate: str
    ) -> list[dict]:
        return [
            episode for episode in catalog.episodes
            if episode.get("airdate") == airdate
        ]

    def get_cached_episode_catalog(self, show_id: int) -> EpisodeCatalog:
        show_id = _positive_show_id(show_id)
        path = self._cache_path(show_id)
        try:
            with path.open("r", encoding="utf-8") as source:
                document = json.load(source)
        except FileNotFoundError as error:
            raise ProviderError("resolver cache is missing") from error
        except (OSError, json.JSONDecodeError) as error:
            raise ProviderError("resolver cache is malformed") from error
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != 1
            or document.get("provider") != self.provider_id
            or document.get("show_id") != show_id
            or not isinstance(document.get("fetched_at"), str)
        ):
            raise ProviderError("resolver cache is malformed")
        episodes = self._validated_episodes(document.get("episodes"))
        return EpisodeCatalog(
            provider=self.provider_id,
            show_id=show_id,
            canonical_name=str(document.get("canonical_name") or ""),
            fetched_at=document["fetched_at"],
            episodes=episodes,
        )
