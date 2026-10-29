# Evaluation

Liner measures its suggestions against a list you write by hand. That list is the only
judgement the measurements rest on, so it should come from a person, not from the model.

## The seeds file

A seeds file is a JSON list. Each entry is one seed artist and the artists you would
count as good neighbours for them. The IDs below are invented, to show the shape:

```json
[
  {
    "name": "Example Singer",
    "mbid": "a0000000-0000-0000-0000-000000000001",
    "good": ["b0000000-0000-0000-0000-000000000002"]
  }
]
```

- Every `mbid` and every entry in `good` is a MusicBrainz artist ID, in lowercase. Find them with `liner search`, or in the artist's musicbrainz.org URL.
- A seed cannot be its own good neighbour.
- Aim for 30 or more seeds. Mix well-documented and obscure artists, and several genres and eras, so the split by documentation means something.

## Running it

Crawl the seeds first, with a little expansion, so the graph holds their neighbourhoods. The evaluation can only score what is stored:

```bash
liner crawl <seed-mbid> --expand 5
liner eval seeds.json --k 10
```

Add `--ablate` to switch each factor off in turn and see what each one contributes.

## What the numbers mean

- **Precision@k**: the share of the top k suggestions that appear in the good list.
- **Recall@k**: the share of the good list that appears in the top k.
- **Coverage**: the share of seeds that received at least k suggestions.
- **Similarity**: the mean similarity between pairs of suggestions in the top k. Lower means more varied.
- **Explained**: the share of suggestions that carry at least one reason.
- **Obscurity split**: precision for the less documented half of the seeds, then for the better documented half. "Documented" means how many recordings the seed has in the stored graph. It is a rough proxy.

Each metric is measured per seed and then averaged, so one seed with many suggestions cannot dominate.

## Limits

- A good list is one person's judgement. Have a few people review yours, which is what the blind test in the brief is for.
- Good neighbours missing from the graph count as misses, so recall is capped by what the crawl stored.
- Numbers from different seeds files are not comparable.
