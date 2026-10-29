"""Profile tests: feedback moves weights by bounded steps, and reset restores the defaults."""

import unittest

from liner.profile import current_weights, give_feedback, reset_profile, set_setting
from liner.scoring import DEFAULT_FAMILIARITY_TARGET, DEFAULT_WEIGHTS, FAMILIARITY_TARGET
from liner.store import Store


def mb(number: int) -> str:
    return f"{number:08x}-0000-0000-0000-{number:012x}"


class FeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.addCleanup(self.store.close)
        self.store.upsert_artist(mb(1), "Seed")
        self.store.upsert_artist(mb(2), "Candidate")
        self.store.upsert_recording(mb(1001), "Shared Song")
        self.store.add_credit(mb(1001), mb(1), "artist credit")
        self.store.add_credit(mb(1001), mb(2), "producer")

    def test_like_raises_the_factors_that_drove_the_suggestion(self) -> None:
        changes = give_feedback(self.store, mb(1), mb(2), "like", when=0.0)

        before, after = changes["credits"]
        self.assertEqual(before, DEFAULT_WEIGHTS["credits"])
        self.assertGreater(after, before)
        self.assertEqual(current_weights(self.store)["credits"], after)
        self.assertEqual(self.store.counts()["feedback events"], 1)

    def test_skip_lowers_weights_but_never_below_zero(self) -> None:
        # With only credits active, its share of the evidence is 1, so a skip takes a full step.
        for factor in ("path", "link", "era"):
            set_setting(self.store, factor, 0.0)
        set_setting(self.store, "credits", 0.05)

        changes = give_feedback(self.store, mb(1), mb(2), "skip", when=0.0)

        self.assertEqual(changes["credits"][1], 0.0)
        self.assertEqual(current_weights(self.store)["credits"], 0.0)

    def test_like_never_goes_above_the_maximum(self) -> None:
        set_setting(self.store, "credits", 1.99)

        changes = give_feedback(self.store, mb(1), mb(2), "like", when=0.0)

        self.assertEqual(changes["credits"][1], 2.0)

    def test_reset_restores_the_defaults(self) -> None:
        give_feedback(self.store, mb(1), mb(2), "like", when=0.0)

        reset_profile(self.store)

        weights = current_weights(self.store)
        self.assertEqual(weights["credits"], DEFAULT_WEIGHTS["credits"])
        self.assertEqual(weights[FAMILIARITY_TARGET], DEFAULT_FAMILIARITY_TARGET)

    def test_unknown_feedback_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            give_feedback(self.store, mb(1), mb(2), "love", when=0.0)

    def test_an_artist_outside_the_suggestions_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            give_feedback(self.store, mb(1), mb(999), "like", when=0.0)

    def test_settings_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            set_setting(self.store, "bogus", 1.0)
        with self.assertRaises(ValueError):
            set_setting(self.store, "credits", 5.0)
        with self.assertRaises(ValueError):
            set_setting(self.store, FAMILIARITY_TARGET, 1.5)

        set_setting(self.store, FAMILIARITY_TARGET, 0.8)

        self.assertEqual(current_weights(self.store)[FAMILIARITY_TARGET], 0.8)


if __name__ == "__main__":
    unittest.main()
