import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liner.cli import main

SEED = "a0000000-0000-0000-0000-000000000001"


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = str(Path(directory.name) / "liner.sqlite")

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_stats_on_an_empty_database(self) -> None:
        code, out, _ = self.run_cli("stats")

        self.assertEqual(code, 0)
        self.assertIn("artists: 0", out)

    def test_rejects_input_that_is_not_an_mbid(self) -> None:
        code, _, err = self.run_cli("crawl", "Radiohead")

        self.assertEqual(code, 1)
        self.assertIn("not a lowercase MusicBrainz ID", err)

    def test_crawl_needs_a_contact_before_any_request(self) -> None:
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("LINER_CONTACT", None)
            code, _, err = self.run_cli("crawl", SEED)

        self.assertEqual(code, 1)
        self.assertIn("LINER_CONTACT", err)

    def test_neighbours_says_so_when_nothing_is_stored(self) -> None:
        code, out, _ = self.run_cli("neighbours", SEED)

        self.assertEqual(code, 0)
        self.assertIn("Nothing stored", out)

    def test_profile_shows_the_defaults_with_familiarity_off(self) -> None:
        code, out, _ = self.run_cli("profile")

        self.assertEqual(code, 0)
        self.assertIn("credits      1.00", out)
        self.assertIn("familiarity  0.00  (off)", out)

    def test_profile_set_and_reset(self) -> None:
        code, out, _ = self.run_cli("profile", "set", "credits", "1.5")
        self.assertEqual(code, 0)
        self.assertIn("credits      1.50", out)

        code, out, _ = self.run_cli("profile", "reset")

        self.assertIn("credits      1.00", out)

    def test_profile_rejects_unknown_settings(self) -> None:
        code, _, err = self.run_cli("profile", "set", "bogus", "1")

        self.assertEqual(code, 1)
        self.assertIn("unknown setting", err)

    def test_feedback_rejects_a_bad_artist_id(self) -> None:
        code, _, err = self.run_cli("feedback", "like", "Radiohead", SEED)

        self.assertEqual(code, 1)
        self.assertIn("not a lowercase MusicBrainz ID", err)

    def test_eval_reports_a_missing_seeds_file(self) -> None:
        code, _, err = self.run_cli("eval", str(Path(self.db).parent / "missing.json"))

        self.assertEqual(code, 1)
        self.assertIn("error", err)


if __name__ == "__main__":
    unittest.main()
