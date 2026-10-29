"""Client for the MusicBrainz web service (WS/2).

Every response is cached in the local store, so a repeated request never reaches
the network. Network requests are spaced at least one second apart, the rate
MusicBrainz asks clients to keep per IP address:
https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from liner import __version__
from liner.store import Store

BASE_URL = "https://musicbrainz.org/ws/2/"
CONTACT_ENV_VAR = "LINER_CONTACT"
MIN_INTERVAL_SECONDS = 1.0
MAX_RETRIES = 3
RETRYABLE_STATUSES = frozenset({429, 503})
_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# (url, headers) -> (status, body). Injected so tests never touch the network.
Fetch = Callable[[str, dict[str, str]], tuple[int, str]]


class MusicBrainzError(RuntimeError):
    """A request failed for good: not found, still rate limited after retries, or no network."""


class ConfigError(RuntimeError):
    """The setup is incomplete, so no request is sent."""


def is_mbid(value: str) -> bool:
    """True for a lowercase MusicBrainz ID, the form used in MusicBrainz URLs."""
    return _MBID.fullmatch(value) is not None


def build_user_agent(contact: str | None) -> str:
    """Name the app, its version and a way to reach its maintainer. MusicBrainz blocks anonymous clients."""
    contact = (contact or "").strip()
    if not contact:
        raise ConfigError(f"Set {CONTACT_ENV_VAR} to a project URL or email address before calling MusicBrainz.")
    return f"Liner/{__version__} ({contact})"


def user_agent_from_env() -> str:
    return build_user_agent(os.environ.get(CONTACT_ENV_VAR))


def urllib_fetch(url: str, headers: dict[str, str]) -> tuple[int, str]:
    """Send one GET request. HTTP error statuses are returned; network failures are raised."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        raise MusicBrainzError(f"network error for {url}: {error.reason}") from error


def _query_string(params: dict[str, str]) -> str:
    # MusicBrainz separates `inc` values with a literal "+", so that one must stay unescaped.
    pairs = sorted({**params, "fmt": "json"}.items())
    return "&".join(
        f"{key}={urllib.parse.quote(value, safe='+' if key == 'inc' else '')}" for key, value in pairs
    )


class MusicBrainzClient:
    def __init__(
        self,
        store: Store,
        user_agent: str,
        *,
        fetch: Fetch = urllib_fetch,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        min_interval: float = MIN_INTERVAL_SECONDS,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._store = store
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._fetch = fetch
        self._clock = clock
        self._sleep = sleep
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._last_request_at: float | None = None
        self.network_requests = 0
        self.cache_hits = 0

    def get(self, endpoint: str, **params: str) -> dict:
        """GET a WS/2 endpoint as JSON, for example get("artist/", query="Radiohead")."""
        url = f"{BASE_URL}{endpoint}?{_query_string(params)}"
        cached = self._store.get_cached(url)
        if cached is not None:
            self.cache_hits += 1
            return json.loads(cached)
        body = self._fetch_with_retries(url)
        self._store.put_cached(url, body, fetched_at=time.time())
        return json.loads(body)

    def _fetch_with_retries(self, url: str) -> str:
        retries_left = self._max_retries
        while True:
            self._wait_for_slot()
            self.network_requests += 1
            status, body = self._fetch(url, self._headers)
            if status == 200:
                return body
            if status not in RETRYABLE_STATUSES or retries_left == 0:
                raise MusicBrainzError(f"MusicBrainz answered HTTP {status} for {url}")
            self._sleep(2 ** (self._max_retries - retries_left))
            retries_left -= 1

    def _wait_for_slot(self) -> None:
        if self._last_request_at is not None:
            wait = self._last_request_at + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()
