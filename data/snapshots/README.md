# CelesTrak data snapshots

Every successful CelesTrak GP query is stored here **verbatim** so that Orbix is
reproducible and can keep working when CelesTrak is unreachable.

## Files

| File | Contents |
|---|---|
| `{cache_key}_{YYYYmmddTHHMMSSZ}.json` | The exact bytes returned by CelesTrak for one query. `cache_key` is derived from the query selector, e.g. `catnr-25544` or `group-stations`; the timestamp is the UTC retrieval time. |
| `manifest.json` | Index of every snapshot: query, URL, retrieval time, HTTP status, record count, newest OMM epoch, and SHA-256 of the payload. |

The manifest lets Orbix answer "how old is this data, and where did it come
from?" without guessing, and the SHA-256 makes it possible to prove a snapshot
was not edited by hand.

## Snapshot semantics

* **Cache.** The newest snapshot for a query is authoritative for 2 hours, which
  matches CelesTrak's GP refresh cadence. Inside that window no HTTP request is
  made.
* **Fallback.** If a request fails, or CelesTrak replies HTTP 403 "GP data has
  not updated since your last successful download", the newest snapshot is used
  and the result is labelled `source="snapshot"` with a note explaining why.
* **Rebuild.** If `manifest.json` is lost, snapshot records are reconstructed
  from the timestamps in the filenames.

## What is committed

Small, stable payloads (ISS, `GROUP=STATIONS`, other groups Orbix displays) are
committed so tests, demos and offline runs work out of the box. Bulk groups
(`active`, Starlink, `last-30-days`, `visual`) are git-ignored: they change every
2 hours and would bloat the repository.

## Provenance

All payloads in this directory come from
<https://celestrak.org/NORAD/elements/gp.php> (CelesTrak, Dr. T.S. Kelso).
No file here is synthetic, hand-edited or interpolated.
