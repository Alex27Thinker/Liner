# Data and licences

Checked on 2026-10-29. **Verified** means I read the official page. **Unverified** means I did not, or the sources disagreed. This is my reading of the licences, not legal advice.

## Datasets

| Dataset | Licence | Status | How Liner uses it |
| --- | --- | --- | --- |
| MusicBrainz core data: artists, recordings, releases, labels, relationships (credits) | CC0 (public domain) | Verified: [Data License](https://musicbrainz.org/doc/About/Data_License), [Database page](https://musicbrainz.org/doc/MusicBrainz_Database) | The credits graph. Used now. |
| MusicBrainz supplementary data: tags and genre associations, ratings, annotations, derived statistics, search indexes, edit history | CC BY-NC-SA 3.0 (non-commercial, attribution, derivatives under the same licence) | Verified: same pages | Not used. Genre and tag scoring would need it. The repo ships no copy of it. |
| MusicBrainz Live Data Feed | CC BY-NC-SA 3.0 | Verified: Data License page | Not used. |
| MusicBrainz server source code | GPL v2 | Verified: Data License page | Not reused. |
| MusicBrainz documentation | CC BY-NC-SA 3.0 | Verified: Data License page | Not copied. |
| ListenBrainz listen data and dumps | CC0 according to secondary sources only | **Unverified.** The ListenBrainz docs, about and FAQ pages I could fetch contain no licence text. AlternativeTo and forum posts say CC0. | Not used. Verify before Phase 5. |
| AcousticBrainz data (could give BPM and key for recordings with MBIDs) | CC0 according to the project's 2014 launch post and homepage (search snippets) | **Unverified.** The snippets are from 2014 and 2020. I believe new submissions stopped in 2022, but that is from memory. | Not used. Check whether the data is still downloadable before relying on it. |
| Spotify recommendation and audio-feature APIs | Restricted for new apps | Not checked; excluded by the brief | Not used. |

## MusicBrainz API rules that shape the code

From [Rate Limiting](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting) (verified):

- The average rate per IP address is about one request per second. Requests over the limit get HTTP 503 until the rate drops.
- Every request needs a User-Agent that lets MusicBrainz contact the maintainers. Blank, Java and Python-urllib agents count as anonymous and are throttled.

What the code does about it: it sends `Liner/<version> (<LINER_CONTACT>)`, refuses to make any request until `LINER_CONTACT` is set, spaces requests one second apart, retries 429 and 503 responses with backoff, and caches every response.

## Code and attribution

- **Liner's own code licence:** not chosen yet. The contributor terms (approved licences, ownership) are not in the repo, so I could not check them. MIT is a common candidate. No LICENSE file until the terms are checked.
- **Attribution:** MusicBrainz core data is CC0, so nothing is required. Supplementary data, if used later, requires credit.

## Audio analysis libraries (not chosen)

For BPM and key from the user's own files, the candidates are librosa and Essentia. From memory, not verified: librosa is ISC-licensed; Essentia's open-source version is AGPL-3.0, with a commercial licence available from its authors. Check both before choosing one.
