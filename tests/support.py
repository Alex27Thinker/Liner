"""Test doubles: a scripted MusicBrainz and a clock that only moves when told to."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from liner.musicbrainz import BASE_URL


class FakeMusicBrainz:
    """Answers requests from a dict keyed by endpoint path, such as "artist/<mbid>". Unknown paths get 404."""

    def __init__(self, routes: dict[str, dict]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        self.urls.append(url)
        path = urlsplit(url).path.removeprefix(urlsplit(BASE_URL).path)
        if path in self.routes:
            return 200, json.dumps(self.routes[path])
        return 404, "{}"


class FakeClock:
    """Stands in for time.monotonic and time.sleep. Sleeping advances the clock instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
