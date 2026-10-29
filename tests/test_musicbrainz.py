import unittest

from liner import __version__
from liner.musicbrainz import ConfigError, MusicBrainzClient, MusicBrainzError, build_user_agent, is_mbid
from liner.store import Store
from tests.support import FakeClock

USER_AGENT = "Liner/test (https://example.org/liner)"


class ScriptedFetch:
    """Replays (status, body) pairs in order and records every request it receives."""

    def __init__(self, *responses: tuple[int, str]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        self.urls.append(url)
        self.headers.append(headers)
        return self.responses.pop(0)


class UserAgentTests(unittest.TestCase):
    def test_contact_is_required(self) -> None:
        for contact in (None, "", "   "):
            with self.subTest(contact=contact), self.assertRaises(ConfigError):
                build_user_agent(contact)

    def test_names_the_app_version_and_contact(self) -> None:
        self.assertEqual(
            build_user_agent("https://example.org/liner"),
            f"Liner/{__version__} (https://example.org/liner)",
        )


class MbidTests(unittest.TestCase):
    def test_accepts_only_lowercase_uuids(self) -> None:
        self.assertTrue(is_mbid("a0000000-0000-0000-0000-000000000001"))
        self.assertFalse(is_mbid("A0000000-0000-0000-0000-000000000001"))
        self.assertFalse(is_mbid("Radiohead"))
        self.assertFalse(is_mbid("a0000000-0000-0000-0000-000000000001/../x"))


class MusicBrainzClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.addCleanup(self.store.close)
        self.clock = FakeClock()

    def make_client(self, fetch: ScriptedFetch, **options: int) -> MusicBrainzClient:
        return MusicBrainzClient(
            self.store,
            USER_AGENT,
            fetch=fetch,
            clock=self.clock.time,
            sleep=self.clock.sleep,
            **options,
        )

    def test_sorts_parameters_and_serves_repeats_from_cache(self) -> None:
        fetch = ScriptedFetch((200, '{"artists": []}'))
        client = self.make_client(fetch)

        self.assertEqual(client.get("artist/", query="Radiohead", limit="5"), {"artists": []})
        self.assertEqual(client.get("artist/", limit="5", query="Radiohead"), {"artists": []})

        self.assertEqual(fetch.urls, ["https://musicbrainz.org/ws/2/artist/?fmt=json&limit=5&query=Radiohead"])
        self.assertEqual(fetch.headers[0]["User-Agent"], USER_AGENT)
        self.assertEqual(client.network_requests, 1)
        self.assertEqual(client.cache_hits, 1)

    def test_keeps_the_plus_separator_in_inc_values(self) -> None:
        fetch = ScriptedFetch((200, "{}"))
        self.make_client(fetch).get("artist/x", inc="recording-rels+artist-rels")
        self.assertIn("inc=recording-rels+artist-rels", fetch.urls[0])

    def test_spaces_network_requests_one_second_apart(self) -> None:
        fetch = ScriptedFetch((200, "{}"), (200, "{}"))
        client = self.make_client(fetch)

        client.get("artist/one")
        client.get("artist/two")

        self.assertEqual(self.clock.sleeps, [1.0])

    def test_retries_rate_limited_responses(self) -> None:
        fetch = ScriptedFetch((503, ""), (200, '{"ok": true}'))
        client = self.make_client(fetch)

        self.assertEqual(client.get("artist/x"), {"ok": True})
        self.assertEqual(client.network_requests, 2)
        self.assertEqual(self.clock.sleeps, [1.0])

    def test_gives_up_after_the_retry_limit(self) -> None:
        fetch = ScriptedFetch((503, ""), (503, ""), (503, ""))
        client = self.make_client(fetch, max_retries=2)

        with self.assertRaises(MusicBrainzError):
            client.get("artist/x")

        self.assertEqual(client.network_requests, 3)
        self.assertEqual(self.clock.sleeps, [1.0, 2.0])

    def test_not_found_fails_without_retrying(self) -> None:
        fetch = ScriptedFetch((404, ""))
        client = self.make_client(fetch)

        with self.assertRaises(MusicBrainzError):
            client.get("artist/missing")

        self.assertEqual(client.network_requests, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_failures_are_not_cached(self) -> None:
        fetch = ScriptedFetch((404, ""), (200, "{}"))
        client = self.make_client(fetch)

        with self.assertRaises(MusicBrainzError):
            client.get("artist/x")
        self.assertEqual(client.get("artist/x"), {})

        self.assertEqual(client.network_requests, 2)
