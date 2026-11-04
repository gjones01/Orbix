#!/usr/bin/env python
"""Step B proof of concept: retrieve and persist genuine CelesTrak GP data.

This script is the reproducible acceptance check for the data-acquisition layer.
It performs one CelesTrak GP query, validates the JSON/OMM structure, writes the
raw payload to ``data/snapshots/`` with a manifest entry, and prints a concise
verification summary.

Usage
-----
    python scripts/verify_celestrak_api.py                       # ISS (25544)
    python scripts/verify_celestrak_api.py --catnr 25544 --refresh
    python scripts/verify_celestrak_api.py --group STATIONS
    python scripts/verify_celestrak_api.py --offline             # snapshots only

Exit code 0 means the data was retrieved, validated, and saved (or served from a
saved snapshot).  Any failure prints the reason and exits non-zero.  No data is
ever fabricated.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.api.celestrak import (  # noqa: E402  (path bootstrap above)
    CelestrakClient,
    CelestrakError,
    DEFAULT_SNAPSHOT_DIR,
    GpFetchResult,
)

ISS_CATALOG_NUMBER = 25544
RULE = "=" * 78


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the CelesTrak GP (OMM/JSON) integration."
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--catnr",
        type=int,
        default=ISS_CATALOG_NUMBER,
        help=f"NORAD catalog number to query (default: {ISS_CATALOG_NUMBER} = ISS)",
    )
    target.add_argument(
        "--group",
        type=str,
        default=None,
        help="CelesTrak group to query instead, e.g. STATIONS or GPS-OPS",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the 2-hour cache and hit CelesTrak (respect the usage policy)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="never touch the network; serve the newest saved snapshot",
    )
    parser.add_argument(
        "--show-raw",
        action="store_true",
        help="also print the raw JSON payload of the first record",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=str,
        default=None,
        help=f"snapshot directory (default: {DEFAULT_SNAPSHOT_DIR})",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="show client debug logging"
    )
    return parser


def summarise(result: GpFetchResult) -> None:
    """Print the human-readable verification summary."""
    freshness = result.freshness()
    print(RULE)
    print("CelesTrak GP retrieval summary")
    print(RULE)
    print(f"Query URL         : {result.url}")
    print(f"HTTP status       : {result.http_status}")
    print(f"Data source       : {result.source}")
    print(f"Records returned  : {len(result.elements)}")
    print(f"Retrieved at (UTC): {result.retrieved_at:%Y-%m-%d %H:%M:%S}")
    print(f"Freshness         : {freshness.summary()}")
    if result.snapshot is not None:
        print(f"Snapshot file     : {result.snapshot.filename}")
        print(f"Snapshot SHA-256  : {result.snapshot.sha256}")
    for note in result.notes:
        print(f"Note              : {note}")

    for element in result.elements:
        print()
        print(f"--- {element.object_name} (NORAD {element.norad_cat_id}) ---")
        print(f"International ID : {element.object_id or 'n/a'}")
        print(f"Element epoch    : {element.epoch:%Y-%m-%d %H:%M:%S.%f} UTC")
        print(f"Epoch age        : {freshness.epoch_age} (at retrieval)")
        period = element.period_minutes
        period_text = f" (Keplerian period {period:.3f} min)" if period else ""
        print(f"Mean motion      : {element.mean_motion:.8f} rev/day{period_text}")
        print(f"Eccentricity     : {element.eccentricity:.7f}")
        print(f"Inclination      : {element.inclination:.4f} deg")
        print(f"RAAN             : {element.ra_of_asc_node:.4f} deg")
        print(f"Arg of pericenter: {element.arg_of_pericenter:.4f} deg")
        print(f"Mean anomaly     : {element.mean_anomaly:.4f} deg")
        print(f"B* drag term     : {element.bstar}")
        print(f"Rev at epoch     : {element.rev_at_epoch}")
        print(f"Element set no.  : {element.element_set_no}")
    print(RULE)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    client = CelestrakClient(
        snapshot_dir=args.snapshot_dir,
        offline=args.offline,
    )

    try:
        if args.group:
            result = client.fetch_by_group(args.group, refresh=args.refresh)
        else:
            result = client.fetch_by_catalog_number(args.catnr, refresh=args.refresh)
    except CelestrakError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "No satellite data was returned. Orbix will not substitute "
            "fabricated elements.",
            file=sys.stderr,
        )
        return 1

    summarise(result)

    if args.show_raw:
        print("\nRaw OMM record as returned by CelesTrak:")
        print(json.dumps(dict(result.elements[0].raw), indent=2))

    print(f"\nSnapshots on disk: {client.snapshot_dir}")
    for record in client.list_snapshots():
        print(
            f"  {record.filename}  records={record.record_count}  "
            f"retrieved={record.retrieved_at:%Y-%m-%dT%H:%M:%SZ}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
