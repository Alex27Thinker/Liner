"""Command-line interface: search, crawl, neighbours, feedback, profile, eval and stats."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from liner.crawl import crawl_artist
from liner.evaluate import Report, ablation, evaluate, load_seeds, split_by_obscurity
from liner.musicbrainz import ConfigError, MusicBrainzClient, MusicBrainzError, is_mbid, user_agent_from_env
from liner.profile import current_weights, give_feedback, reset_profile, set_setting
from liner.scoring import FACTORS, FAMILIARITY_TARGET, Scorer
from liner.store import Store

DB_ENV_VAR = "LINER_DB"
DEFAULT_DB_PATH = Path("data") / "liner.sqlite"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="liner", description="Explore music through the people behind it.")
    parser.add_argument("--db", type=Path, help=f"SQLite file (default: ${DB_ENV_VAR}, else {DEFAULT_DB_PATH})")
    commands = parser.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="find artists by name (needs LINER_CONTACT)")
    search.add_argument("name")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(run=_run_search)

    crawl = commands.add_parser("crawl", help="store an artist's credits and collaborators (needs LINER_CONTACT)")
    crawl.add_argument("mbid", help="MusicBrainz artist ID, from `search` or the artist's musicbrainz.org URL")
    crawl.add_argument("--max-recordings", type=int, default=50, help="recordings to read in full per artist (default: 50)")
    crawl.add_argument("--expand", type=int, default=0, help="also crawl the N artists most closely credited with it")
    crawl.set_defaults(run=_run_crawl)

    near = commands.add_parser("neighbours", help="rank connected artists, each with the evidence for it")
    near.add_argument("mbid")
    near.add_argument("--limit", type=int, default=20)
    near.add_argument(
        "--no-diversify",
        action="store_true",
        help="keep the plain ranking instead of spreading the list out",
    )
    near.set_defaults(run=_run_neighbours)

    feedback = commands.add_parser("feedback", help="like or skip a suggestion, nudging the profile")
    feedback.add_argument("kind", choices=["like", "skip"])
    feedback.add_argument("seed", help="the seed artist's MusicBrainz ID")
    feedback.add_argument("artist", help="the suggested artist's MusicBrainz ID")
    feedback.set_defaults(run=_run_feedback)

    profile = commands.add_parser("profile", help="show or change the factor weights")
    profile.add_argument("action", nargs="?", choices=["show", "set", "reset"], default="show")
    profile.add_argument("name", nargs="?", help=f"setting for `set`: {', '.join(FACTORS + (FAMILIARITY_TARGET,))}")
    profile.add_argument("value", nargs="?", type=float, help="new value for `set`")
    profile.set_defaults(run=_run_profile)

    evaluation = commands.add_parser("eval", help="measure suggestions against a seeds file")
    evaluation.add_argument("seeds", type=Path, help="JSON file of seeds and their good neighbours")
    evaluation.add_argument("--k", type=int, default=10)
    evaluation.add_argument("--ablate", action="store_true", help="switch each factor off in turn")
    evaluation.set_defaults(run=_run_eval)

    commands.add_parser("stats", help="count what is stored locally").set_defaults(run=_run_stats)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = args.db or os.environ.get(DB_ENV_VAR) or DEFAULT_DB_PATH
    try:
        with Store(db_path) as store:
            return args.run(args, store)
    except (ConfigError, MusicBrainzError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run_search(args: argparse.Namespace, store: Store) -> int:
    client = MusicBrainzClient(store, user_agent_from_env())
    result = client.get("artist/", query=args.name, limit=str(args.limit))
    for artist in result.get("artists", []):
        print(f"{artist['id']}  {artist['name']}  ({artist.get('type', 'unknown type')}, {artist.get('country', 'no country')})")
    return 0


def _run_crawl(args: argparse.Namespace, store: Store) -> int:
    _require_mbid(args.mbid)
    client = MusicBrainzClient(store, user_agent_from_env())
    report = crawl_artist(client, store, args.mbid, max_recordings=args.max_recordings, expand=args.expand)
    extra = f", plus {len(report.expanded)} connected artist(s)" if report.expanded else ""
    print(
        f"Crawled {report.artist_name}{extra}: {report.recordings_read} recordings read, "
        f"{report.network_requests} network requests, {report.cache_hits} from cache."
    )
    return 0


def _run_neighbours(args: argparse.Namespace, store: Store) -> int:
    _require_mbid(args.mbid)
    found = Scorer(store).suggest(args.mbid, limit=args.limit, diversify=not args.no_diversify)
    if not found:
        print("Nothing stored for this artist yet. Run `crawl` on it first.")
    for suggestion in found:
        print(f"{suggestion.name} [{suggestion.mbid}]  score {suggestion.score:.2f}, confidence {suggestion.confidence:.0%}")
        for reason in suggestion.reasons:
            print(f"    {reason}")
        if suggestion.missing:
            print(f"    no data for: {', '.join(suggestion.missing)}")
    return 0


def _run_feedback(args: argparse.Namespace, store: Store) -> int:
    _require_mbid(args.seed)
    _require_mbid(args.artist)
    changes = give_feedback(store, args.seed, args.artist, args.kind, when=time.time())
    for factor, (before, after) in changes.items():
        print(f"{factor:<12} {before:.2f} -> {after:.2f}")
    return 0


def _run_profile(args: argparse.Namespace, store: Store) -> int:
    if args.action == "set":
        if args.name is None or args.value is None:
            raise ValueError("usage: liner profile set NAME VALUE")
        set_setting(store, args.name, args.value)
    elif args.action == "reset":
        reset_profile(store)
    weights = current_weights(store)
    for factor in FACTORS:
        note = "  (off)" if weights[factor] == 0 else ""
        print(f"{factor:<12} {weights[factor]:.2f}{note}")
    print(f"{FAMILIARITY_TARGET:<12} {weights[FAMILIARITY_TARGET]:.2f}")
    return 0


def _run_eval(args: argparse.Namespace, store: Store) -> int:
    seeds = load_seeds(args.seeds)
    scorer = Scorer(store)
    if args.ablate:
        for label, report in ablation(scorer, seeds, k=args.k).items():
            print(f"{label:<18} {_summary(report)}")
        return 0
    report = evaluate(scorer, seeds, k=args.k)
    print(_summary(report))
    lower, upper = split_by_obscurity(report)
    print(f"precision, less documented half of seeds: {lower:.2f}; better documented half: {upper:.2f}")
    return 0


def _summary(report: Report) -> str:
    return (
        f"coverage {report.coverage:.0%}  precision@{report.k} {report.precision:.2f}  "
        f"recall@{report.k} {report.recall:.2f}  similarity {report.similarity:.2f} (lower is more varied)  "
        f"explained {report.explained:.0%}"
    )


def _run_stats(args: argparse.Namespace, store: Store) -> int:
    for label, count in store.counts().items():
        print(f"{label}: {count}")
    return 0


def _require_mbid(value: str) -> None:
    if not is_mbid(value):
        raise ValueError(f"not a lowercase MusicBrainz ID: {value!r}")
