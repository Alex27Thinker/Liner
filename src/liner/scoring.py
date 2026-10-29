"""Ranked suggestions from the credits graph, each with the evidence behind it.

A candidate is scored on five factors. Each one is a simple statistic over the stored
data, and nothing here is trained. A factor with no data for a candidate contributes
nothing, and the confidence says how much of the weighting had data behind it.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from liner.store import Store

FACTORS = ("credits", "path", "link", "era", "familiarity")
FAMILIARITY_TARGET = "familiarity_target"
DEFAULT_FAMILIARITY_TARGET = 0.5

# Familiarity stays off until there is a popularity signal worth trusting.
DEFAULT_WEIGHTS: dict[str, float] = {
    "credits": 1.0,
    "path": 0.6,
    "link": 0.8,
    "era": 0.4,
    "familiarity": 0.0,
}

# How much a shared credit says about two artists. A producer says more than
# "artist credit" on a compilation does.
ROLE_WEIGHTS: dict[str, float] = {
    "producer": 1.0,
    "engineer": 0.9,
    "mix": 0.9,
    "mastering": 0.8,
    "vocal": 0.8,
    "performer": 0.8,
    "instrument": 0.7,
    "arranger": 0.7,
    "composer": 0.6,
    "lyricist": 0.6,
    "artist credit": 0.5,
}
DEFAULT_ROLE_WEIGHT = 0.5
LINK_WEIGHTS: dict[str, float] = {"member of band": 1.0}
DEFAULT_LINK_WEIGHT = 0.6

ERA_SCALE_YEARS = 10.0
RESTART = 0.15
MAX_ITERATIONS = 100
PATH_SIZE_EXPONENT = 0.5
MMR_LAMBDA = 0.7
POOL_SIZE = 100


@dataclass
class Graph:
    """The stored credits graph, read into memory for one query."""

    names: dict[str, str] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    # artist -> recording -> [(role, detail)]
    credits: dict[str, dict[str, list[tuple[str, str]]]] = field(default_factory=dict)
    # recording -> artists credited on it
    members: dict[str, set[str]] = field(default_factory=dict)
    # artist -> linked artist -> strongest relation between them, in both directions
    links: dict[str, dict[str, str]] = field(default_factory=dict)
    years: dict[str, int] = field(default_factory=dict)
    crawled: set[str] = field(default_factory=set)
    # how many credits carry each instrument, for specificity
    instrument_uses: dict[str, int] = field(default_factory=dict)


@dataclass
class Scored:
    mbid: str
    name: str
    score: float
    confidence: float
    contributions: dict[str, float]
    missing: tuple[str, ...]


@dataclass
class Suggestion:
    mbid: str
    name: str
    score: float
    confidence: float
    contributions: dict[str, float]
    reasons: tuple[str, ...]
    missing: tuple[str, ...]


def resolve_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
    """The defaults, with any values the caller or the profile sets on top."""
    resolved = dict(DEFAULT_WEIGHTS)
    resolved[FAMILIARITY_TARGET] = DEFAULT_FAMILIARITY_TARGET
    resolved.update(weights or {})
    return resolved


def load_graph(store: Store) -> Graph:
    graph = Graph()
    conn = store.conn
    graph.names = {row["mbid"]: row["name"] for row in conn.execute("SELECT mbid, name FROM artist")}
    graph.titles = {row["mbid"]: row["title"] for row in conn.execute("SELECT mbid, title FROM recording")}
    for row in conn.execute("SELECT recording_mbid, artist_mbid, role, detail FROM credit"):
        recording, artist, role, detail = row["recording_mbid"], row["artist_mbid"], row["role"], row["detail"]
        graph.credits.setdefault(artist, {}).setdefault(recording, []).append((role, detail))
        graph.members.setdefault(recording, set()).add(artist)
        if role == "instrument" and detail:
            graph.instrument_uses[detail] = graph.instrument_uses.get(detail, 0) + 1
    for row in conn.execute("SELECT artist_mbid, other_mbid, relation FROM artist_link"):
        _add_link(graph, row["artist_mbid"], row["other_mbid"], row["relation"])
        _add_link(graph, row["other_mbid"], row["artist_mbid"], row["relation"])
    graph.years = {
        row["recording_mbid"]: row["year"] for row in conn.execute("SELECT recording_mbid, year FROM recording_year")
    }
    graph.crawled = {row["mbid"] for row in conn.execute("SELECT mbid FROM crawled_artist")}
    return graph


def _add_link(graph: Graph, artist: str, other: str, relation: str) -> None:
    kept = graph.links.setdefault(artist, {}).get(other)
    if kept is None or _link_strength(relation) > _link_strength(kept):
        graph.links[artist][other] = relation


def _link_strength(relation: str) -> float:
    return LINK_WEIGHTS.get(relation, DEFAULT_LINK_WEIGHT)


def credit_weight(graph: Graph, role: str, detail: str) -> float:
    """How much one credit says. A rare instrument says more than a common one."""
    weight = ROLE_WEIGHTS.get(role, DEFAULT_ROLE_WEIGHT)
    if role == "instrument" and detail:
        uses = graph.instrument_uses.get(detail, 1)
        weight *= max(0.3, min(1.0, 2.0 / (1.0 + math.log(1 + uses))))
    return weight


def _best_weight(graph: Graph, artist: str, recording: str) -> float:
    return max(credit_weight(graph, role, detail) for role, detail in graph.credits[artist][recording])


def _overlap(graph: Graph, a: str, b: str) -> tuple[float, tuple[str, ...]]:
    """How much two artists' credits overlap. Big recordings and prolific artists count for less."""
    mine = graph.credits.get(a, {})
    theirs = graph.credits.get(b, {})
    shared = tuple(sorted(mine.keys() & theirs.keys()))
    if not shared:
        return 0.0, ()
    total = sum(
        _best_weight(graph, a, recording) * _best_weight(graph, b, recording) / len(graph.members[recording])
        for recording in shared
    )
    return total / math.sqrt(len(mine) * len(theirs)), shared


def _link_weight(graph: Graph, a: str, b: str) -> float:
    relation = graph.links.get(a, {}).get(b)
    return 0.0 if relation is None else _link_strength(relation)


def _normalise(options: list[tuple[str, float]]) -> list[tuple[str, float]]:
    total = sum(weight for _, weight in options)
    if total <= 0:
        return []
    return [(target, weight / total) for target, weight in options]


def _path_mass(graph: Graph, seed: str) -> dict[str, float]:
    """Random walk with restart from the seed. Returns the mass that lands on each artist."""
    moves: dict[str, list[tuple[str, float]]] = {}
    for artist in set(graph.credits) | set(graph.links):
        options = [(f"r:{recording}", 1.0) for recording in graph.credits.get(artist, {})]
        options += [(f"a:{other}", _link_weight(graph, artist, other)) for other in graph.links.get(artist, {})]
        moves[f"a:{artist}"] = _normalise(options)
    for recording, artists in graph.members.items():
        options = [(f"a:{artist}", _best_weight(graph, artist, recording)) for artist in artists]
        moves[f"r:{recording}"] = _normalise(options)

    start = f"a:{seed}"
    mass = {start: 1.0}
    for _ in range(MAX_ITERATIONS):
        following = {start: RESTART}
        for node, value in mass.items():
            for target, probability in moves.get(node, []):
                following[target] = following.get(target, 0.0) + (1 - RESTART) * value * probability
        change = sum(abs(following.get(node, 0.0) - mass.get(node, 0.0)) for node in following.keys() | mass.keys())
        mass = following
        if change < 1e-10:
            break
    return {node[2:]: value for node, value in mass.items() if node.startswith("a:") and node != start}


def _path_score(graph: Graph, artist: str, mass: float) -> float:
    """Mass reaching the artist, discounted by how much the artist has done.
    Without the discount, prolific artists collect mass just by existing."""
    recordings = max(1, len(graph.credits.get(artist, {})))
    return mass / recordings**PATH_SIZE_EXPONENT


def _era(graph: Graph, artist: str) -> float | None:
    years = [graph.years[recording] for recording in graph.credits.get(artist, {}) if recording in graph.years]
    return float(statistics.median(years)) if years else None


def _familiarity(graph: Graph, artist: str, most: int) -> float | None:
    """How much of the stored catalogue the artist has, on a log scale from 0 to 1."""
    count = len(graph.credits.get(artist, {}))
    if count == 0:
        return None
    return math.log(1 + count) / math.log(1 + most)


def select_diverse(
    ranked: Sequence[Scored],
    similarity: Callable[[str, str], float],
    limit: int,
    lam: float = MMR_LAMBDA,
) -> list[Scored]:
    """Maximal marginal relevance: each pick trades its score against how close it is to the picks already made."""
    chosen: list[Scored] = []
    remaining = list(ranked)
    while remaining and len(chosen) < limit:
        best = max(remaining, key=lambda item: _mmr_value(item, chosen, similarity, lam))
        chosen.append(best)
        remaining.remove(best)
    return chosen


def _mmr_value(item: Scored, chosen: Sequence[Scored], similarity: Callable[[str, str], float], lam: float) -> float:
    if not chosen:
        return item.score
    closeness = max(similarity(item.mbid, other.mbid) for other in chosen)
    return lam * item.score - (1 - lam) * closeness


class Scorer:
    """Scores artists against a seed. The graph is loaded once, so repeat queries are cheap."""

    def __init__(self, store: Store) -> None:
        self.graph = load_graph(store)
        self._overlaps: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
        most = max((len(recordings) for recordings in self.graph.credits.values()), default=1)
        self._most_recordings = max(1, most)

    def overlap(self, a: str, b: str) -> tuple[float, tuple[str, ...]]:
        key = (a, b) if a <= b else (b, a)
        if key not in self._overlaps:
            self._overlaps[key] = _overlap(self.graph, a, b)
        return self._overlaps[key]

    def similarity(self, a: str, b: str) -> float:
        """How alike two artists are, from 0 to 1. Used to spread suggestions apart."""
        return min(1.0, max(self.overlap(a, b)[0], _link_weight(self.graph, a, b)))

    def suggest(
        self,
        seed: str,
        *,
        weights: dict[str, float] | None = None,
        limit: int | None = 20,
        diversify: bool = True,
    ) -> list[Suggestion]:
        graph = self.graph
        if seed not in graph.credits and seed not in graph.links:
            return []
        resolved = resolve_weights(weights)
        active = {factor: resolved[factor] for factor in FACTORS if resolved[factor] > 0}
        total = sum(active.values())
        if total <= 0:
            return []
        target = resolved[FAMILIARITY_TARGET]

        mass = _path_mass(graph, seed)
        raw = {artist: self._raw(seed, artist, mass[artist], target) for artist in mass if mass[artist] > 0}
        max_credits = max((values["credits"] for values in raw.values()), default=0.0)
        max_path = max((values["path"] for values in raw.values()), default=0.0)

        scored = []
        for artist, values in raw.items():
            available = self._available(seed, artist, values)
            factor_values = {
                "credits": values["credits"] / max_credits if max_credits > 0 else 0.0,
                "path": values["path"] / max_path if max_path > 0 else 0.0,
                "link": values["link"],
                "era": values["era"] or 0.0,
                "familiarity": values["familiarity"] or 0.0,
            }
            contributions = {factor: active[factor] * factor_values[factor] / total for factor in active}
            scored.append(
                Scored(
                    mbid=artist,
                    name=graph.names.get(artist, artist),
                    score=sum(contributions.values()),
                    confidence=sum(active[factor] for factor in active if factor in available) / total,
                    contributions=contributions,
                    missing=tuple(factor for factor in active if factor not in available),
                )
            )
        scored.sort(key=lambda item: (-item.score, item.name, item.mbid))
        pool = scored[:POOL_SIZE]
        cap = len(pool) if limit is None else limit
        chosen = select_diverse(pool, self.similarity, cap) if diversify else pool[:cap]
        return [self._explain(seed, item, target) for item in chosen]

    def _raw(self, seed: str, artist: str, path_mass: float, target: float) -> dict[str, float | None]:
        graph = self.graph
        seed_era, era = _era(graph, seed), _era(graph, artist)
        familiarity = _familiarity(graph, artist, self._most_recordings)
        return {
            "credits": self.overlap(seed, artist)[0],
            "path": _path_score(graph, artist, path_mass),
            "link": _link_weight(graph, seed, artist),
            "era": None if seed_era is None or era is None else math.exp(-abs(era - seed_era) / ERA_SCALE_YEARS),
            "familiarity": None if familiarity is None else 1.0 - abs(familiarity - target),
        }

    def _available(self, seed: str, artist: str, values: dict[str, float | None]) -> set[str]:
        graph = self.graph
        available = {"path"}
        if graph.credits.get(seed) and graph.credits.get(artist):
            available.add("credits")
        if seed in graph.crawled or artist in graph.crawled:
            available.add("link")
        if values["era"] is not None:
            available.add("era")
        if values["familiarity"] is not None:
            available.add("familiarity")
        return available

    def _explain(self, seed: str, item: Scored, target: float) -> Suggestion:
        graph = self.graph
        artist = item.mbid
        reasons: list[str] = []
        _, shared = self.overlap(seed, artist)
        if shared:
            roles = sorted({role for recording in shared for role, _ in graph.credits[artist][recording]})
            titles = [graph.titles.get(recording, recording) for recording in shared[:3]]
            reasons.append(f"shared {len(shared)} recording(s) as {', '.join(roles)}: {', '.join(titles)}")
        relation = graph.links.get(seed, {}).get(artist)
        if relation:
            reasons.append(f"linked: {relation}")
        if not shared and not relation:
            via = self._via(seed, artist)
            if via:
                reasons.append(f"two steps away, via {graph.names.get(via, via)}")
        seed_era, era = _era(graph, seed), _era(graph, artist)
        if seed_era is not None and era is not None:
            reasons.append(f"era: {era:.0f} vs {seed_era:.0f}")
        if "familiarity" in item.contributions:
            familiarity = _familiarity(graph, artist, self._most_recordings)
            if familiarity is not None:
                reasons.append(f"familiarity {familiarity:.2f} vs target {target:.2f}")
        return Suggestion(
            mbid=artist,
            name=item.name,
            score=item.score,
            confidence=item.confidence,
            contributions=item.contributions,
            reasons=tuple(reasons),
            missing=item.missing,
        )

    def _via(self, seed: str, artist: str) -> str | None:
        """The closest artist that sits between the seed and this one, if there is one."""
        best, best_value = None, 0.0
        for middle in self._neighbours(seed):
            if middle == artist:
                continue
            value = self.similarity(seed, middle) * self.similarity(middle, artist)
            if value > best_value:
                best, best_value = middle, value
        return best

    def _neighbours(self, artist: str) -> set[str]:
        graph = self.graph
        found = set(graph.links.get(artist, {}))
        for recording in graph.credits.get(artist, {}):
            found |= graph.members[recording]
        found.discard(artist)
        return found


def suggest(
    store: Store,
    seed: str,
    *,
    weights: dict[str, float] | None = None,
    limit: int | None = 20,
    diversify: bool = True,
) -> list[Suggestion]:
    return Scorer(store).suggest(seed, weights=weights, limit=limit, diversify=diversify)
