"""Crawl tests run against invented fixtures shaped like WS/2 responses. The names and credits are made up."""

import unittest

from liner.crawl import crawl_artist
from liner.musicbrainz import MusicBrainzClient
from liner.store import Store
from tests.support import FakeClock, FakeMusicBrainz

USER_AGENT = "Liner/test (https://example.org/liner)"
SEED = "a0000000-0000-0000-0000-000000000001"
BAND = "b0000000-0000-0000-0000-000000000002"
PRODUCER = "c0000000-0000-0000-0000-000000000003"
MUSICIAN = "d0000000-0000-0000-0000-000000000004"
SONG_ONE = "e0000000-0000-0000-0000-000000000011"
SONG_TWO = "e0000000-0000-0000-0000-000000000012"


def artist(mbid: str, name: str, artist_type: str) -> dict:
    return {"id": mbid, "name": name, "sort-name": name, "type": artist_type}


ROUTES = {
    f"artist/{SEED}": {
        **artist(SEED, "Test Singer", "Person"),
        "country": "GB",
        "relations": [
            {
                "type": "vocal",
                "target-type": "recording",
                "direction": "backward",
                "recording": {"id": SONG_ONE, "title": "First Song", "length": 255000},
                "attributes": ["lead vocals"],
            },
            {
                "type": "member of band",
                "target-type": "artist",
                "direction": "forward",
                "artist": artist(BAND, "Test Band", "Group"),
                "attributes": [],
                "begin": "2001",
                "end": None,
            },
            {
                "type": "official homepage",
                "target-type": "url",
                "direction": "forward",
                "url": {"resource": "https://example.invalid/"},
            },
        ],
    },
    f"artist/{BAND}": {**artist(BAND, "Test Band", "Group"), "relations": []},
    "recording": {
        "recording-count": 2,
        "recording-offset": 0,
        "recordings": [
            {"id": SONG_ONE, "title": "First Song", "length": 255000},
            {"id": SONG_TWO, "title": "Second Song", "length": 309000},
        ],
    },
    f"recording/{SONG_ONE}": {
        "id": SONG_ONE,
        "title": "First Song",
        "length": 255000,
        "artist-credit": [{"name": "Test Band", "joinphrase": "", "artist": artist(BAND, "Test Band", "Group")}],
        "releases": [
            {"id": "release-1", "date": "1997-06-16"},
            {"id": "release-2", "date": "2003"},
        ],
        "relations": [
            {
                "type": "producer",
                "target-type": "artist",
                "direction": "backward",
                "artist": artist(PRODUCER, "Test Producer", "Person"),
                "attributes": [],
            },
            {
                "type": "vocal",
                "target-type": "artist",
                "direction": "backward",
                "artist": artist(SEED, "Test Singer", "Person"),
                "attributes": ["lead vocals"],
            },
        ],
    },
    f"recording/{SONG_TWO}": {
        "id": SONG_TWO,
        "title": "Second Song",
        "length": 309000,
        "artist-credit": [{"name": "Test Band", "artist": artist(BAND, "Test Band", "Group")}],
        "relations": [
            {
                "type": "instrument",
                "target-type": "artist",
                "direction": "backward",
                "artist": artist(MUSICIAN, "Test Multi-instrumentalist", "Person"),
                "attributes": ["synthesizer"],
            },
        ],
    },
}


class CrawlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.addCleanup(self.store.close)
        self.clock = FakeClock()
        self.fake = FakeMusicBrainz(ROUTES)
        self.client = MusicBrainzClient(
            self.store, USER_AGENT, fetch=self.fake, clock=self.clock.time, sleep=self.clock.sleep
        )

    def test_stores_artists_recordings_credits_and_links(self) -> None:
        report = crawl_artist(self.client, self.store, SEED)

        self.assertEqual(report.artist_name, "Test Singer")
        self.assertEqual(report.recordings_read, 2)
        self.assertEqual(report.expanded, ())
        self.assertEqual(report.network_requests, 4)  # artist, browse page, and two recordings
        counts = self.store.counts()
        self.assertEqual(counts["artists"], 4)
        self.assertEqual(counts["recordings"], 2)
        self.assertEqual(counts["credits"], 5)
        self.assertEqual(counts["artist links"], 1)
        self.assertEqual(counts["crawled artists"], 1)
        links = self.store.conn.execute("SELECT artist_mbid, other_mbid, relation FROM artist_link").fetchall()
        self.assertEqual([tuple(row) for row in links], [(SEED, BAND, "member of band")])

    def test_the_earliest_release_year_is_stored_for_a_recording(self) -> None:
        crawl_artist(self.client, self.store, SEED)

        years = self.store.conn.execute("SELECT recording_mbid, year FROM recording_year").fetchall()

        self.assertEqual([tuple(row) for row in years], [(SONG_ONE, 1997)])

    def test_expansion_also_crawls_the_most_closely_credited_artist(self) -> None:
        report = crawl_artist(self.client, self.store, SEED, expand=1)

        self.assertEqual(report.expanded, (BAND,))
        self.assertEqual(self.store.counts()["crawled artists"], 2)
        # Four for the seed, then the band's artist page and its catalogue page.
        self.assertEqual(report.network_requests, 6)

    def test_repeating_a_crawl_is_served_from_the_cache(self) -> None:
        crawl_artist(self.client, self.store, SEED)
        urls_before = len(self.fake.urls)

        report = crawl_artist(self.client, self.store, SEED)

        self.assertEqual(report.network_requests, 0)
        self.assertEqual(report.cache_hits, 4)
        self.assertEqual(len(self.fake.urls), urls_before)

    def test_max_recordings_limits_how_many_are_read(self) -> None:
        report = crawl_artist(self.client, self.store, SEED, max_recordings=1)

        self.assertEqual(report.recordings_read, 1)
        self.assertEqual(report.network_requests, 2)  # the artist, then the one recording
        self.assertEqual(self.store.counts()["recordings"], 1)

    def test_rejects_a_limit_below_one(self) -> None:
        with self.assertRaises(ValueError):
            crawl_artist(self.client, self.store, SEED, max_recordings=0)
        self.assertEqual(self.fake.urls, [])

    def test_rejects_a_negative_expansion(self) -> None:
        with self.assertRaises(ValueError):
            crawl_artist(self.client, self.store, SEED, expand=-1)
        self.assertEqual(self.fake.urls, [])
