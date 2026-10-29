"""The profile: factor weights, and feedback that nudges them.

Feedback moves each factor by a fixed, bounded step, in proportion to how much that
factor contributed to the artist. Nothing is learned or trained. Every weight is
visible, and `reset` puts the defaults back.
"""

from __future__ import annotations

from liner.scoring import FACTORS, FAMILIARITY_TARGET, Scorer, resolve_weights
from liner.store import Store

STEP = 0.1
MIN_WEIGHT = 0.0
MAX_WEIGHT = 2.0
KINDS = ("like", "skip")
SETTINGS = (*FACTORS, FAMILIARITY_TARGET)


def current_weights(store: Store) -> dict[str, float]:
    return resolve_weights(store.get_profile_weights())


def set_setting(store: Store, name: str, value: float) -> None:
    if name not in SETTINGS:
        raise ValueError(f"unknown setting {name!r}; choose from {', '.join(SETTINGS)}")
    low, high = (0.0, 1.0) if name == FAMILIARITY_TARGET else (MIN_WEIGHT, MAX_WEIGHT)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    store.set_profile_weight(name, value)


def reset_profile(store: Store) -> None:
    store.clear_profile_weights()


def give_feedback(
    store: Store,
    seed: str,
    artist: str,
    kind: str,
    *,
    when: float,
) -> dict[str, tuple[float, float]]:
    """Like or skip a suggestion. Returns each changed factor's weight before and after."""
    if kind not in KINDS:
        raise ValueError(f"feedback must be one of: {', '.join(KINDS)}")
    weights = current_weights(store)
    suggestions = Scorer(store).suggest(seed, weights=weights, limit=None, diversify=False)
    matches = [item for item in suggestions if item.mbid == artist]
    if not matches:
        raise ValueError("that artist is not among the suggestions for this seed")
    contributions = matches[0].contributions
    total = sum(contributions.values())
    if total <= 0:
        raise ValueError("there is no evidence to learn from for that artist")
    direction = 1.0 if kind == "like" else -1.0
    changes: dict[str, tuple[float, float]] = {}
    for factor, contribution in contributions.items():
        before = weights[factor]
        after = min(MAX_WEIGHT, max(MIN_WEIGHT, before + direction * STEP * contribution / total))
        store.set_profile_weight(factor, after)
        changes[factor] = (before, after)
    store.add_feedback(seed, artist, kind, when)
    return changes
