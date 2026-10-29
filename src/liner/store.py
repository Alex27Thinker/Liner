"""SQLite storage. One file holds the HTTP cache, the credits graph and the profile."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    url TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    fetched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS artist (
    mbid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sort_name TEXT,
    type TEXT,
    country TEXT,
    disambiguation TEXT
);

CREATE TABLE IF NOT EXISTS recording (
    mbid TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    length_ms INTEGER
);

-- Who was credited on which recording, and how. `detail` carries extras such as
-- an instrument name, and is empty when there is nothing to add.
CREATE TABLE IF NOT EXISTS credit (
    recording_mbid TEXT NOT NULL REFERENCES recording (mbid),
    artist_mbid TEXT NOT NULL REFERENCES artist (mbid),
    role TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (recording_mbid, artist_mbid, role, detail)
);
CREATE INDEX IF NOT EXISTS credit_by_artist ON credit (artist_mbid);

-- Direct artist-to-artist relations such as "member of band". Each row reads
-- as: artist_mbid <relation> other_mbid.
CREATE TABLE IF NOT EXISTS artist_link (
    artist_mbid TEXT NOT NULL REFERENCES artist (mbid),
    other_mbid TEXT NOT NULL REFERENCES artist (mbid),
    relation TEXT NOT NULL,
    begin_date TEXT,
    end_date TEXT,
    PRIMARY KEY (artist_mbid, other_mbid, relation)
);

CREATE TABLE IF NOT EXISTS crawled_artist (
    mbid TEXT PRIMARY KEY,
    crawled_at REAL NOT NULL
);

-- Year of the earliest release a recording appears on. Used for the era factor.
CREATE TABLE IF NOT EXISTS recording_year (
    recording_mbid TEXT PRIMARY KEY REFERENCES recording (mbid),
    year INTEGER NOT NULL
);

-- Factor weights the user has set or nudged. Factors not listed use the defaults.
CREATE TABLE IF NOT EXISTS profile_weight (
    factor TEXT PRIMARY KEY,
    weight REAL NOT NULL
);

-- Every like or skip, so each nudge can be traced to the suggestion that caused it.
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_mbid TEXT NOT NULL,
    artist_mbid TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('like', 'skip')),
    created_at REAL NOT NULL
);
"""

_COUNTED_TABLES = {
    "artists": "artist",
    "recordings": "recording",
    "credits": "credit",
    "artist links": "artist_link",
    "crawled artists": "crawled_artist",
    "recording years": "recording_year",
    "feedback events": "feedback",
    "cached responses": "http_cache",
}


class Store:
    """A SQLite database opened once per process. Not safe to share across threads."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        # Commit before closing, so a command that writes never loses its changes on exit.
        self.conn.commit()
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def get_cached(self, url: str) -> str | None:
        row = self.conn.execute("SELECT body FROM http_cache WHERE url = ?", (url,)).fetchone()
        return None if row is None else row["body"]

    def put_cached(self, url: str, body: str, fetched_at: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO http_cache (url, body, fetched_at) VALUES (?, ?, ?)",
            (url, body, fetched_at),
        )
        self.conn.commit()

    def upsert_artist(
        self,
        mbid: str,
        name: str,
        *,
        sort_name: str | None = None,
        artist_type: str | None = None,
        country: str | None = None,
        disambiguation: str | None = None,
    ) -> None:
        # A partial record (an artist seen only inside a relation) must not erase
        # details stored earlier, so COALESCE keeps the old value when the new one is empty.
        self.conn.execute(
            """
            INSERT INTO artist (mbid, name, sort_name, type, country, disambiguation)
            VALUES (:mbid, :name, :sort_name, :type, :country, :disambiguation)
            ON CONFLICT (mbid) DO UPDATE SET
                name = excluded.name,
                sort_name = COALESCE(excluded.sort_name, artist.sort_name),
                type = COALESCE(excluded.type, artist.type),
                country = COALESCE(excluded.country, artist.country),
                disambiguation = COALESCE(excluded.disambiguation, artist.disambiguation)
            """,
            {
                "mbid": mbid,
                "name": name,
                "sort_name": sort_name,
                "type": artist_type,
                "country": country,
                "disambiguation": disambiguation,
            },
        )

    def upsert_recording(self, mbid: str, title: str, *, length_ms: int | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO recording (mbid, title, length_ms) VALUES (?, ?, ?)
            ON CONFLICT (mbid) DO UPDATE SET
                title = excluded.title,
                length_ms = COALESCE(excluded.length_ms, recording.length_ms)
            """,
            (mbid, title, length_ms),
        )

    def add_credit(self, recording_mbid: str, artist_mbid: str, role: str, detail: str = "") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO credit (recording_mbid, artist_mbid, role, detail) VALUES (?, ?, ?, ?)",
            (recording_mbid, artist_mbid, role, detail),
        )

    def add_link(
        self,
        artist_mbid: str,
        other_mbid: str,
        relation: str,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO artist_link (artist_mbid, other_mbid, relation, begin_date, end_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (artist_mbid, other_mbid, relation, begin_date, end_date),
        )

    def mark_crawled(self, artist_mbid: str, when: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO crawled_artist (mbid, crawled_at) VALUES (?, ?)",
            (artist_mbid, when),
        )

    def set_recording_year(self, recording_mbid: str, year: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO recording_year (recording_mbid, year) VALUES (?, ?)",
            (recording_mbid, year),
        )

    def get_profile_weights(self) -> dict[str, float]:
        return {row["factor"]: row["weight"] for row in self.conn.execute("SELECT factor, weight FROM profile_weight")}

    def set_profile_weight(self, factor: str, weight: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO profile_weight (factor, weight) VALUES (?, ?)",
            (factor, weight),
        )

    def clear_profile_weights(self) -> None:
        self.conn.execute("DELETE FROM profile_weight")

    def add_feedback(self, seed_mbid: str, artist_mbid: str, kind: str, when: float) -> None:
        self.conn.execute(
            "INSERT INTO feedback (seed_mbid, artist_mbid, kind, created_at) VALUES (?, ?, ?, ?)",
            (seed_mbid, artist_mbid, kind, when),
        )

    def counts(self) -> dict[str, int]:
        return {
            label: self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for label, table in _COUNTED_TABLES.items()
        }
