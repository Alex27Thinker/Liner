# Interview story

Working draft, filled in as the project is built. Nothing under Results has been measured yet.

## Problem

Most music recommendations give a name and no reason: "listeners of X also like Y". A listener who trusts the list cannot check it or steer it. If the recommender learns from what someone already plays, it tends to return close copies of that taste, which is the filter bubble. The working hypothesis behind Liner is that visible reasons make it easier to step outside the bubble on purpose.

## Design decision: a credits graph, not a black box

Liner links artists through the people behind the music: producers, engineers, session musicians, bands and labels. Each link is a path in the data that a person can read. Those paths are the explanation. So a suggestion can say "shared two recordings, as producer", and the user can check the claim against MusicBrainz. A learned model would give a score with no such path.

## Technical depth

- **Graph modelling.** Artists and recordings form a bipartite graph, with credit edges that carry a role and an optional detail. The artist-to-artist view is a projection computed at query time with a join, not stored. Direct links such as "member of band" are kept as their own table.
- **Search.** Artist search goes through the MusicBrainz web service. Fuzzy seed matching and ranking come in Phase 2.
- **Caching.** Every response is stored in SQLite under its full URL. The cache also lets an interrupted crawl resume without re-fetching.
- **Rate limits.** The client spaces requests one second apart, retries 429 and 503 responses with backoff, and refuses to send anything without a contact in the User-Agent. Those are MusicBrainz's rules, checked against its documentation.
- **Shortest path.** Bridge mode (Phase 4) will use breadth-first search over the credits graph. Not built yet.

## Ethics

- **Transparency.** Every link in the output shows its evidence. There is no hidden score.
- **No data collection.** No accounts. The only network traffic is lookups to MusicBrainz. Listening history, if imported in Phase 5, is processed on the user's machine.
- **Serendipity versus engagement.** Skips are a sign of mismatch, not a number to maximise. The radio should be judged on whether people keep what it suggests.

## Results

| Measure | Result |
| --- | --- |
| Dataset size | None from the live API yet. Fixture data only. |
| Query speed | Not measured |
| Tests | 27 unit tests pass (`python -m unittest`, 2026-10-29). Fixtures only; no live API calls. |
| Seeds with known good neighbours (target 30+) | Not started |
| Held-out co-listens | Not started (needs ListenBrainz, Phase 5) |
| Blind test: radio versus random same-genre baseline | Not started (needs the radio, Phase 3) |
| Skip rate in sessions | Not started (needs the radio) |
| Users and stars | None yet |
