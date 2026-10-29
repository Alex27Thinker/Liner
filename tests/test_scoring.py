"""Scoring tests on small, hand-made credit graphs. Each test checks one rule the scores must follow."""

import unittest

from liner.scoring import (
    DEFAULT_FAMILIARITY_TARGET,
    DEFAULT_WEIGHTS,
    FAMILIARITY_TARGET,
    Scored,
    Scorer,
    resolve_weights,
    select_diverse,
)
from liner.store import Store


def mb(number: int) -> str:
    """A well-formed MusicBrainz ID for a small test number."""
    return f"{number:08x}-0000-0000-0000-{number:012x}"


SEED = mb(1)


class World:
    """Builds a credits graph by number. Artists use numbers below 1000; recordings use 1000 and up."""

    def __init__(self) -> None:
        self.store = Store()
        self._artists: set[int] = set()
        self._recordings: set[int] = set()

    def close(self) -> None:
        self.store.close()

    def artist(self, number: int, name: str | None = None) -> None:
        self.store.upsert_artist(mb(number), name or f"Artist {number}")
        self._artists.add(number)

    def recording(self, number: int, title: str | None = None, year: int | None = None) -> None:
        if number not in self._recordings:
            self.store.upsert_recording(mb(1000 + number), title or f"Track {number}")
            self._recordings.add(number)
        if year is not None:
            self.store.set_recording_year(mb(1000 + number), year)

    def credit(self, recording: int, artist: int, role: str, detail: str = "") -> None:
        if artist not in self._artists:
            self.artist(artist)
        self.recording(recording)
        self.store.add_credit(mb(1000 + recording), mb(artist), role, detail)

    def link(self, artist: int, other: int, relation: str = "member of band") -> None:
        for number in (artist, other):
            if number not in self._artists:
                self.artist(number)
        self.store.add_link(mb(artist), mb(other), relation)


class ScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def scorer(self) -> Scorer:
        return Scorer(self.world.store)

    def by_mbid(self, seed: str = SEED, **options) -> dict:
        options.setdefault("limit", None)
        options.setdefault("diversify", False)
        return {item.mbid: item for item in self.scorer().suggest(seed, **options)}

    def test_a_recording_with_many_people_on_it_counts_for_less(self) -> None:
        self.world.credit(1, 1, "artist credit")  # the seed, on a two-person recording
        self.world.credit(1, 2, "producer")
        self.world.credit(2, 1, "artist credit")  # the seed, on a forty-two-person recording
        self.world.credit(2, 3, "producer")
        for number in range(100, 140):
            self.world.credit(2, number, "artist credit")

        scorer = self.scorer()

        self.assertGreater(scorer.overlap(SEED, mb(2))[0], scorer.overlap(SEED, mb(3))[0])

    def test_a_prolific_artist_is_scaled_down(self) -> None:
        self.world.credit(1, 1, "artist credit")
        self.world.credit(1, 2, "producer")  # prolific: thirty recordings in all
        self.world.credit(2, 1, "artist credit")
        self.world.credit(2, 3, "producer")  # occasional: one recording
        for number in range(1, 30):
            self.world.credit(10 + number, 2, "producer")
            self.world.credit(10 + number, 200 + number, "artist credit")

        scorer = self.scorer()

        self.assertGreater(scorer.overlap(SEED, mb(3))[0], scorer.overlap(SEED, mb(2))[0])

    def test_a_producer_says_more_than_an_artist_credit(self) -> None:
        self.world.credit(1, 1, "artist credit")
        self.world.credit(1, 2, "producer")
        self.world.credit(2, 1, "artist credit")
        self.world.credit(2, 3, "artist credit")

        scorer = self.scorer()

        self.assertGreater(scorer.overlap(SEED, mb(2))[0], scorer.overlap(SEED, mb(3))[0])

    def test_a_rare_instrument_says_more_than_a_common_one(self) -> None:
        self.world.credit(1, 1, "artist credit")
        self.world.credit(1, 2, "instrument", "synthesizer")
        self.world.credit(2, 1, "artist credit")
        self.world.credit(2, 3, "instrument", "guitar")
        for number in range(100, 150):  # fifty other guitarists make the guitar common
            self.world.credit(3, number, "instrument", "guitar")

        scorer = self.scorer()

        self.assertGreater(scorer.overlap(SEED, mb(2))[0], scorer.overlap(SEED, mb(3))[0])

    def test_two_steps_away_is_found_and_explained(self) -> None:
        self.world.credit(1, 1, "artist credit")
        self.world.credit(1, 2, "artist credit")  # the seed and a middle artist
        self.world.credit(2, 2, "artist credit")
        self.world.credit(2, 3, "artist credit")  # the middle artist and a far artist
        self.world.credit(3, 4, "artist credit")  # connected to neither
        self.world.credit(3, 5, "artist credit")

        found = self.by_mbid()

        self.assertIn(mb(3), found)
        self.assertIn("two steps away, via Artist 2", found[mb(3)].reasons)
        self.assertNotIn(mb(4), found)
        self.assertNotIn(mb(5), found)
        self.assertNotIn(SEED, found)

    def test_direct_links_are_scored_and_explained(self) -> None:
        self.world.artist(1, "Seed Artist")
        self.world.link(1, 2)
        self.world.store.mark_crawled(SEED, 0.0)

        linked = self.by_mbid()[mb(2)]

        self.assertIn("linked: member of band", linked.reasons)
        self.assertGreater(linked.contributions["link"], 0)

    def test_era_compares_release_years_and_marks_missing_data(self) -> None:
        self.world.recording(1, "First Song", year=1999)
        self.world.credit(1, 1, "artist credit")
        self.world.credit(1, 2, "artist credit")
        self.world.recording(2, "Later Song", year=2001)
        self.world.credit(2, 2, "artist credit")
        self.world.credit(2, 3, "artist credit")
        self.world.recording(5, "Undated Song")
        self.world.credit(5, 1, "artist credit")
        self.world.credit(5, 4, "artist credit")
        self.world.store.mark_crawled(SEED, 0.0)

        found = self.by_mbid()

        dated = found[mb(2)]
        self.assertIn("era: 2000 vs 1999", dated.reasons)
        self.assertNotIn("era", dated.missing)
        self.assertAlmostEqual(dated.confidence, 1.0)
        undated = found[mb(4)]
        self.assertIn("era", undated.missing)
        self.assertLess(undated.confidence, 1.0)

    def test_familiarity_is_off_until_it_is_weighted(self) -> None:
        self.world.credit(1, 1, "artist credit")
        self.world.credit(1, 2, "artist credit")

        off = self.by_mbid()[mb(2)]
        on = self.by_mbid(weights={"familiarity": 0.5})[mb(2)]

        self.assertNotIn("familiarity", off.contributions)
        self.assertIn("familiarity", on.contributions)
        self.assertTrue(any(reason.startswith("familiarity") for reason in on.reasons))

    def test_shared_recordings_are_named_in_the_reason(self) -> None:
        self.world.recording(1, "First Song")
        self.world.credit(1, 1, "artist credit")
        self.world.credit(1, 2, "producer")

        found = self.by_mbid()

        self.assertIn("shared 1 recording(s) as producer: First Song", found[mb(2)].reasons)

    def test_unknown_seed_has_no_suggestions(self) -> None:
        self.assertEqual(self.scorer().suggest(mb(999)), [])

    def test_limit_caps_the_list_and_none_returns_everything(self) -> None:
        for artist in (2, 3, 4):
            self.world.credit(1, artist, "artist credit")
        self.world.credit(1, 1, "artist credit")

        scorer = self.scorer()

        self.assertEqual(len(scorer.suggest(SEED, limit=None, diversify=False)), 3)
        self.assertEqual(len(scorer.suggest(SEED, limit=2)), 2)

    def test_weights_merge_over_the_defaults(self) -> None:
        resolved = resolve_weights({"credits": 2.0})

        self.assertEqual(resolved["credits"], 2.0)
        self.assertEqual(resolved["path"], DEFAULT_WEIGHTS["path"])
        self.assertEqual(resolved[FAMILIARITY_TARGET], DEFAULT_FAMILIARITY_TARGET)


class DiversitySelectionTests(unittest.TestCase):
    def test_a_near_duplicate_gives_way_to_a_distinct_artist(self) -> None:
        ranked = [
            Scored("a", "A", 1.0, 1.0, {}, ()),
            Scored("b", "B", 0.95, 1.0, {}, ()),
            Scored("c", "C", 0.7, 1.0, {}, ()),
        ]

        def similarity(x: str, y: str) -> float:
            return 0.9 if {x, y} == {"a", "b"} else 0.0

        chosen = select_diverse(ranked, similarity, limit=2)

        self.assertEqual([item.mbid for item in chosen], ["a", "c"])


if __name__ == "__main__":
    unittest.main()
