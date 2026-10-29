import sqlite3
import tempfile
import unittest
from pathlib import Path

from liner.store import Store

ARTIST = "a0000000-0000-0000-0000-000000000001"
RECORDING = "e0000000-0000-0000-0000-000000000011"


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.addCleanup(self.store.close)

    def test_cached_response_round_trip(self) -> None:
        self.assertIsNone(self.store.get_cached("https://example.org/a"))
        self.store.put_cached("https://example.org/a", "{}", fetched_at=1.0)
        self.assertEqual(self.store.get_cached("https://example.org/a"), "{}")

    def test_partial_artist_update_keeps_earlier_details(self) -> None:
        self.store.upsert_artist(ARTIST, "Old Name", sort_name="Name, Old", country="GB")
        self.store.upsert_artist(ARTIST, "New Name")

        row = self.store.conn.execute("SELECT name, sort_name, country FROM artist").fetchone()

        self.assertEqual(tuple(row), ("New Name", "Name, Old", "GB"))

    def test_same_credit_is_stored_once(self) -> None:
        self.store.upsert_artist(ARTIST, "Artist")
        self.store.upsert_recording(RECORDING, "Track")

        self.store.add_credit(RECORDING, ARTIST, "producer")
        self.store.add_credit(RECORDING, ARTIST, "producer")
        self.store.add_credit(RECORDING, ARTIST, "instrument", "guitar")

        self.assertEqual(self.store.counts()["credits"], 2)

    def test_credit_needs_a_known_artist_and_recording(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_credit(RECORDING, ARTIST, "producer")

    def test_empty_store_counts_zero(self) -> None:
        self.assertEqual(set(self.store.counts().values()), {0})

    def test_closing_keeps_what_was_written(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "liner.sqlite"
        store = Store(path)
        store.upsert_artist(ARTIST, "Artist")

        store.close()  # no explicit commit: closing must keep the write
        reopened = Store(path)
        self.addCleanup(reopened.close)

        self.assertEqual(reopened.counts()["artists"], 1)

    def test_profile_weights_round_trip_and_clear(self) -> None:
        self.store.set_profile_weight("credits", 1.5)
        self.assertEqual(self.store.get_profile_weights(), {"credits": 1.5})

        self.store.clear_profile_weights()

        self.assertEqual(self.store.get_profile_weights(), {})

    def test_recording_years_and_feedback_are_counted(self) -> None:
        self.store.upsert_artist(ARTIST, "Artist")
        self.store.upsert_recording(RECORDING, "Track")

        self.store.set_recording_year(RECORDING, 1999)
        self.store.add_feedback(ARTIST, ARTIST, "like", 0.0)

        counts = self.store.counts()
        self.assertEqual(counts["recording years"], 1)
        self.assertEqual(counts["feedback events"], 1)
