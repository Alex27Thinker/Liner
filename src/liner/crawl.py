"""Fill the credits graph for one artist from MusicBrainz.

A crawl reads the artist, then up to `max_recordings` of their recordings in full,
and everyone credited on those recordings. It is a sample: the first recordings
MusicBrainz lists for the artist, not the whole catalogue. With `expand`, the artists
most closely credited with the seed are crawled too, which gives the scoring a second
step of connections to work with.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from liner.musicbrainz import MusicBrainzClient
from liner.store import Store

ARTIST_INCLUDES = "recording-rels+artist-rels"
RECORDING_INCLUDES = "artist-credits+artist-rels+releases"
BROWSE_PAGE_SIZE = 100
ARTIST_CREDIT_ROLE = "artist credit"

# Artists credited alongside `seed` on the most recordings, not yet crawled.
_EXPANSION_SQL = """
SELECT other.artist_mbid AS mbid
FROM credit AS mine
JOIN credit AS other
  ON other.recording_mbid = mine.recording_mbid
 AND other.artist_mbid <> mine.artist_mbid
WHERE mine.artist_mbid = :seed
  AND other.artist_mbid NOT IN (SELECT mbid FROM crawled_artist)
GROUP BY other.artist_mbid
ORDER BY COUNT(DISTINCT other.recording_mbid) DESC, other.artist_mbid
LIMIT :limit
"""


@dataclass(frozen=True)
class CrawlReport:
    artist_mbid: str
    artist_name: str
    recordings_read: int
    expanded: tuple[str, ...]
    network_requests: int
    cache_hits: int


def crawl_artist(
    client: MusicBrainzClient,
    store: Store,
    artist_mbid: str,
    *,
    max_recordings: int = 50,
    expand: int = 0,
) -> CrawlReport:
    if max_recordings < 1:
        raise ValueError("max_recordings must be at least 1")
    if expand < 0:
        raise ValueError("expand cannot be negative")
    requests_before = client.network_requests
    hits_before = client.cache_hits

    seed, name, recordings_read = _crawl_one(client, store, artist_mbid, max_recordings)
    expanded = []
    for other in _expansion_candidates(store, seed, expand):
        _crawl_one(client, store, other, max_recordings)
        expanded.append(other)
    return CrawlReport(
        artist_mbid=seed,
        artist_name=name,
        recordings_read=recordings_read,
        expanded=tuple(expanded),
        network_requests=client.network_requests - requests_before,
        cache_hits=client.cache_hits - hits_before,
    )


def _crawl_one(
    client: MusicBrainzClient,
    store: Store,
    artist_mbid: str,
    max_recordings: int,
) -> tuple[str, str, int]:
    artist = client.get(f"artist/{artist_mbid}", inc=ARTIST_INCLUDES)
    seed = artist["id"]
    _upsert_artist(store, artist)
    relation_recordings = _store_artist_relations(store, artist)
    recordings = _candidate_recordings(client, seed, relation_recordings, max_recordings)
    for recording_mbid in recordings:
        _store_recording(store, client.get(f"recording/{recording_mbid}", inc=RECORDING_INCLUDES))
        store.commit()  # commit per recording, so an interrupted crawl keeps its progress
    store.mark_crawled(seed, time.time())
    store.commit()
    return seed, artist["name"], len(recordings)


def _expansion_candidates(store: Store, seed: str, limit: int) -> list[str]:
    if limit == 0:
        return []
    rows = store.conn.execute(_EXPANSION_SQL, {"seed": seed, "limit": limit}).fetchall()
    return [row["mbid"] for row in rows]


def _candidate_recordings(
    client: MusicBrainzClient,
    artist_mbid: str,
    relation_recordings: list[str],
    limit: int,
) -> list[str]:
    """Recordings to read in full: the artist's own relations first, then the catalogue."""
    candidates = dict.fromkeys(relation_recordings)  # ordered, without repeats
    offset = 0
    while len(candidates) < limit:
        page = client.get("recording", artist=artist_mbid, limit=str(BROWSE_PAGE_SIZE), offset=str(offset))
        recordings = page.get("recordings", [])
        candidates.update(dict.fromkeys(recording["id"] for recording in recordings))
        offset += len(recordings)
        if not recordings or offset >= page.get("recording-count", 0):
            break
    return list(candidates)[:limit]


def _store_artist_relations(store: Store, artist: dict[str, Any]) -> list[str]:
    """Store the artist's relations to recordings and to other artists. Returns the recording IDs."""
    seed = artist["id"]
    recording_mbids: list[str] = []
    for relation in artist.get("relations", []):
        target_type = relation.get("target-type")
        if target_type == "recording":
            recording = relation["recording"]
            store.upsert_recording(recording["id"], recording.get("title", ""), length_ms=recording.get("length"))
            store.add_credit(recording["id"], seed, relation["type"], _detail(relation))
            recording_mbids.append(recording["id"])
        elif target_type == "artist":
            other = relation["artist"]
            _upsert_artist(store, other)
            # MusicBrainz's direction flag says which side this artist sits on, so the
            # link is stored in reading order: "source <relation> target".
            if relation.get("direction") == "backward":
                source, target = other["id"], seed
            else:
                source, target = seed, other["id"]
            store.add_link(source, target, relation["type"], relation.get("begin"), relation.get("end"))
    return recording_mbids


def _store_recording(store: Store, recording: dict[str, Any]) -> None:
    mbid = recording["id"]
    store.upsert_recording(mbid, recording.get("title", ""), length_ms=recording.get("length"))
    year = _release_year(recording)
    if year is not None:
        store.set_recording_year(mbid, year)
    for credit in recording.get("artist-credit", []):
        artist = credit.get("artist")
        if artist is not None:
            _upsert_artist(store, artist)
            store.add_credit(mbid, artist["id"], ARTIST_CREDIT_ROLE)
    for relation in recording.get("relations", []):
        if relation.get("target-type") == "artist":
            artist = relation["artist"]
            _upsert_artist(store, artist)
            store.add_credit(mbid, artist["id"], relation["type"], _detail(relation))


def _release_year(recording: dict[str, Any]) -> int | None:
    """The year of the earliest release that has a date, if there is one."""
    years = []
    for release in recording.get("releases", []):
        date = release.get("date") or ""
        if len(date) >= 4 and date[:4].isdigit():
            years.append(int(date[:4]))
    return min(years) if years else None


def _upsert_artist(store: Store, artist: dict[str, Any]) -> None:
    store.upsert_artist(
        artist["id"],
        artist["name"],
        sort_name=artist.get("sort-name"),
        artist_type=artist.get("type"),
        country=artist.get("country"),
        disambiguation=artist.get("disambiguation"),
    )


def _detail(relation: dict[str, Any]) -> str:
    return ", ".join(relation.get("attributes", []))
