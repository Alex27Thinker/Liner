"""Measure suggestion quality against a hand-built list of good neighbours.

A seeds file is a JSON list. Each entry names a seed artist by MusicBrainz ID and lists
the artist IDs that a knowledgeable listener would count as good neighbours:

    [{"name": "Example Singer", "mbid": "<artist id>", "good": ["<artist id>"]}]

Metrics are measured per seed and then averaged. Nothing here is a trained model.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from liner.musicbrainz import is_mbid
from liner.scoring import FACTORS, Scorer, resolve_weights


@dataclass(frozen=True)
class Seed:
    name: str
    mbid: str
    good: frozenset[str]


@dataclass(frozen=True)
class SeedResult:
    seed: Seed
    suggested: tuple[str, ...]
    precision: float
    recall: float
    similarity: float
    explained: float | None
    recordings: int


@dataclass(frozen=True)
class Report:
    k: int
    results: tuple[SeedResult, ...]

    @property
    def coverage(self) -> float:
        """Share of seeds that received at least k suggestions."""
        return _mean(1.0 if len(result.suggested) >= self.k else 0.0 for result in self.results)

    @property
    def precision(self) -> float:
        return _mean(result.precision for result in self.results)

    @property
    def recall(self) -> float:
        return _mean(result.recall for result in self.results)

    @property
    def similarity(self) -> float:
        return _mean(result.similarity for result in self.results)

    @property
    def explained(self) -> float:
        return _mean(result.explained for result in self.results if result.explained is not None)


def load_seeds(path: Path) -> list[Seed]:
    """Read and check a seeds file. Each error names the entry at fault."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("a seeds file must be a non-empty JSON list")
    seeds = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"seed {index} must be an object")
        name = str(entry.get("name", f"seed {index}"))
        mbid = entry.get("mbid")
        good = entry.get("good")
        if not isinstance(mbid, str) or not is_mbid(mbid):
            raise ValueError(f"seed {index} ({name}): mbid is not a lowercase MusicBrainz ID")
        if not isinstance(good, list) or not good:
            raise ValueError(f"seed {index} ({name}): good needs at least one MusicBrainz ID")
        for other in good:
            if not isinstance(other, str) or not is_mbid(other):
                raise ValueError(f"seed {index} ({name}): {other!r} is not a lowercase MusicBrainz ID")
            if other == mbid:
                raise ValueError(f"seed {index} ({name}): a seed cannot be its own good neighbour")
        seeds.append(Seed(name=name, mbid=mbid, good=frozenset(good)))
    return seeds


def evaluate(
    scorer: Scorer,
    seeds: list[Seed],
    *,
    weights: dict[str, float] | None = None,
    k: int = 10,
) -> Report:
    results = []
    for seed in seeds:
        suggestions = scorer.suggest(seed.mbid, weights=weights, limit=k)
        suggested = tuple(item.mbid for item in suggestions)
        hits = len(set(suggested) & seed.good)
        explained = None if not suggestions else sum(1 for item in suggestions if item.reasons) / len(suggestions)
        results.append(
            SeedResult(
                seed=seed,
                suggested=suggested,
                precision=hits / k,
                recall=hits / len(seed.good),
                similarity=_mean(scorer.similarity(a, b) for a, b in _pairs(suggested)),
                explained=explained,
                recordings=len(scorer.graph.credits.get(seed.mbid, {})),
            )
        )
    return Report(k=k, results=tuple(results))


def ablation(scorer: Scorer, seeds: list[Seed], *, k: int = 10) -> dict[str, Report]:
    """The full model, then the model with each factor switched off in turn."""
    baseline = resolve_weights()
    reports = {"all factors": evaluate(scorer, seeds, weights=baseline, k=k)}
    for factor in FACTORS:
        weights = dict(baseline)
        weights[factor] = 0.0
        reports[f"without {factor}"] = evaluate(scorer, seeds, weights=weights, k=k)
    return reports


def split_by_obscurity(report: Report) -> tuple[float, float]:
    """Mean precision for the less documented half of the seeds, then for the better documented half.
    'Documented' means how many recordings the seed has in the stored graph."""
    ordered = sorted(report.results, key=lambda result: result.recordings)
    middle = len(ordered) // 2
    return _mean(result.precision for result in ordered[:middle]), _mean(
        result.precision for result in ordered[middle:]
    )


def _pairs(ids: tuple[str, ...]) -> list[tuple[str, str]]:
    return [(a, b) for index, a in enumerate(ids) for b in ids[index + 1 :]]


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return mean(items) if items else 0.0
