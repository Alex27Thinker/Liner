"""Evaluation tests: the seeds file is checked, and each metric follows its definition."""

import json
import tempfile
import unittest
from pathlib import Path

from liner.evaluate import Report, Seed, SeedResult, ablation, evaluate, load_seeds, split_by_obscurity
from liner.scoring import FACTORS, Scorer
from liner.store import Store


def mb(number: int) -> str:
    return f"{number:08x}-0000-0000-0000-{number:012x}"


class LoadSeedsTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "seeds.json"

    def write(self, payload: object) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_reads_a_valid_file(self) -> None:
        self.write([{"name": "Seed", "mbid": mb(1), "good": [mb(2)]}])

        seeds = load_seeds(self.path)

        self.assertEqual(seeds[0].name, "Seed")
        self.assertEqual(seeds[0].good, frozenset({mb(2)}))

    def test_a_bad_seed_id_is_reported_against_its_entry(self) -> None:
        self.write([{"name": "Seed", "mbid": "Radiohead", "good": [mb(2)]}])

        with self.assertRaisesRegex(ValueError, r"seed 1 \(Seed\)"):
            load_seeds(self.path)

    def test_a_seed_cannot_be_its_own_good_neighbour(self) -> None:
        self.write([{"name": "Seed", "mbid": mb(1), "good": [mb(1)]}])

        with self.assertRaisesRegex(ValueError, "own good neighbour"):
            load_seeds(self.path)

    def test_an_empty_file_is_rejected(self) -> None:
        self.write([])

        with self.assertRaises(ValueError):
            load_seeds(self.path)


class MetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.addCleanup(self.store.close)
        # The seed shares one recording with each of two candidates.
        for number, name in ((1, "Seed"), (2, "Good"), (3, "Other")):
            self.store.upsert_artist(mb(number), name)
        self.store.upsert_recording(mb(1001), "Shared A")
        self.store.upsert_recording(mb(1002), "Shared B")
        self.store.add_credit(mb(1001), mb(1), "artist credit")
        self.store.add_credit(mb(1001), mb(2), "producer")
        self.store.add_credit(mb(1002), mb(1), "artist credit")
        self.store.add_credit(mb(1002), mb(3), "artist credit")
        self.scorer = Scorer(self.store)

    def test_precision_recall_coverage_and_explanation(self) -> None:
        seed = Seed("Seed", mb(1), frozenset({mb(2)}))

        report = evaluate(self.scorer, [seed], k=2)

        result = report.results[0]
        self.assertEqual(result.precision, 0.5)
        self.assertEqual(result.recall, 1.0)
        self.assertEqual(report.coverage, 1.0)
        self.assertEqual(report.explained, 1.0)
        self.assertEqual(evaluate(self.scorer, [seed], k=3).coverage, 0.0)

    def test_recall_counts_good_neighbours_that_were_never_suggested(self) -> None:
        seed = Seed("Seed", mb(1), frozenset({mb(2), mb(99)}))

        result = evaluate(self.scorer, [seed], k=2).results[0]

        self.assertEqual(result.recall, 0.5)

    def test_ablation_has_a_full_run_and_one_run_per_factor(self) -> None:
        seed = Seed("Seed", mb(1), frozenset({mb(2)}))

        reports = ablation(self.scorer, [seed], k=2)

        self.assertEqual(set(reports), {"all factors", *(f"without {factor}" for factor in FACTORS)})


class ObscuritySplitTests(unittest.TestCase):
    def test_seeds_are_split_by_how_documented_they_are(self) -> None:
        def result(recordings: int, precision: float) -> SeedResult:
            seed = Seed(f"seed with {recordings} recordings", mb(1), frozenset({mb(2)}))
            return SeedResult(seed, (), precision, 0.0, 0.0, None, recordings)

        report = Report(k=10, results=(result(20, 0.8), result(1, 0.2), result(10, 0.6), result(2, 0.4)))

        lower, upper = split_by_obscurity(report)

        self.assertAlmostEqual(lower, 0.3)
        self.assertAlmostEqual(upper, 0.7)


if __name__ == "__main__":
    unittest.main()
