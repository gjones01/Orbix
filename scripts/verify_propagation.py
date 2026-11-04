#!/usr/bin/env python
"""Step C proof of concept: propagate real ISS elements with SGP4.

Loads the ISS element set through the CelesTrak client (snapshot or live), then
propagates it with Skyfield/SGP4 and reports the position in every coordinate
system Orbix uses, together with an independent cross-check against the ``sgp4``
library and a hand-written TEME -> ECEF rotation.

Usage
-----
    python scripts/verify_propagation.py                     # ISS at its epoch
    python scripts/verify_propagation.py --when now
    python scripts/verify_propagation.py --offsets 0,15,30,45
    python scripts/verify_propagation.py --group GPS-OPS --limit 3
    python scripts/verify_propagation.py --online            # allow network
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.api.celestrak import CelestrakClient, CelestrakError, GpElement  # noqa: E402
from src.orbital.propagation import (  # noqa: E402
    OrbitalState,
    PropagationError,
    get_timescale,
    native_sgp4_state,
    propagate,
)

UTC = timezone.utc
ISS_CATALOG_NUMBER = 25544
RULE = "=" * 78


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify SGP4 orbital propagation.")
    parser.add_argument(
        "--when",
        default="epoch",
        help="'epoch' (default), 'now', or an ISO-8601 UTC timestamp",
    )
    parser.add_argument(
        "--offsets",
        default="0,15,30,45",
        help="comma-separated minutes after the reference instant to sample",
    )
    parser.add_argument(
        "--group",
        default=None,
        help="propagate a CelesTrak group instead of a single satellite, e.g. GPS-OPS",
    )
    parser.add_argument("--catnr", type=int, default=ISS_CATALOG_NUMBER)
    parser.add_argument("--limit", type=int, default=1, help="max satellites to report")
    parser.add_argument(
        "--online",
        action="store_true",
        help="allow CelesTrak network access (default: committed snapshot only)",
    )
    parser.add_argument(
        "--use-iers",
        action="store_true",
        help="load IERS Earth-orientation data instead of Skyfield's built-in tables",
    )
    return parser


def gmst_degrees(jd_ut1: float) -> float:
    """IAU-82 GMST in degrees (Vallado eq. 3-47), implemented independently."""
    days = jd_ut1 - 2451545.0
    centuries = days / 36525.0
    theta = (
        280.46061837
        + 360.98564736629 * days
        + 0.000387933 * centuries**2
        - centuries**3 / 38710000.0
    )
    return theta % 360.0


def teme_to_ecef(teme_km, jd_ut1: float):
    """Rotate TEME into ECEF about the z axis by -GMST."""
    theta = math.radians(gmst_degrees(jd_ut1))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    x, y, z = teme_km
    return (cos_t * x + sin_t * y, -sin_t * x + cos_t * y, z)


def resolve_reference(element: GpElement, when: str) -> datetime:
    if when == "epoch":
        return element.epoch
    if when == "now":
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def print_state(state: OrbitalState) -> None:
    print(f"  UTC instant      : {state.timestamp:%Y-%m-%dT%H:%M:%S.%f}Z")
    print(f"  Element epoch    : {state.epoch:%Y-%m-%dT%H:%M:%S.%f}Z")
    print(
        "  Element age      : "
        f"{state.element_age.total_seconds() / 3600.0:+.3f} h "
        f"({state.element_age.total_seconds() / 86400.0:+.3f} d)"
    )
    print("  --- WGS-84 geodetic (Earth surface) ---")
    print(f"  latitude         : {state.latitude_deg:+.6f} deg")
    print(f"  longitude        : {state.longitude_deg:+.6f} deg")
    print(f"  altitude         : {state.altitude_km:.4f} km above the ellipsoid")
    print("  --- Earth-fixed Cartesian (ITRS/ECEF): use for 3D rendering ---")
    print(
        "  x, y, z          : "
        f"{state.ecef_km[0]:+.4f}, {state.ecef_km[1]:+.4f}, {state.ecef_km[2]:+.4f} km"
    )
    print("  --- Inertial Cartesian (GCRS/ICRS): orbit geometry ---")
    print(
        "  x, y, z          : "
        f"{state.gcrs_km[0]:+.4f}, {state.gcrs_km[1]:+.4f}, {state.gcrs_km[2]:+.4f} km"
    )
    print(f"  speed (inertial) : {state.speed_km_s:.6f} km/s")
    print(f"  radius           : {state.radius_km:.4f} km")
    print(f"  period           : {state.period_minutes:.4f} min")
    print(f"  inclination      : {state.inclination_deg:.4f} deg")
    for warning in state.warnings:
        print(f"  WARNING          : {warning}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timescale = get_timescale(use_iers_earth_orientation=args.use_iers)
    print(RULE)
    print("SGP4 propagation verification")
    print(RULE)
    print(
        "Timescale         : Skyfield "
        + ("IERS finals2000A.all" if args.use_iers else "built-in tables (no network)")
    )

    client = CelestrakClient(offline=not args.online)
    try:
        if args.group:
            fetched = client.fetch_by_group(args.group)
        else:
            fetched = client.fetch_by_catalog_number(args.catnr)
    except CelestrakError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Element source    : {fetched.source} ({fetched.url})")
    print(f"Freshness         : {fetched.freshness().summary()}")
    print(f"Satellites        : {len(fetched.elements)}")

    elements = list(fetched.elements)[: max(1, args.limit)]
    offsets = [float(part) for part in args.offsets.split(",") if part.strip()]

    for element in elements:
        reference = resolve_reference(element, args.when)
        print()
        print(RULE)
        print(f"{element.object_name} (NORAD {element.norad_cat_id})")
        print(RULE)
        for offset in offsets:
            when = reference + timedelta(minutes=offset)
            try:
                state = propagate(element, when, timescale=timescale)
            except PropagationError as exc:
                print(f"  offset +{offset:g} min: FAILED {type(exc).__name__}: {exc}")
                continue
            print(f"[offset {offset:+g} min from reference]")
            print_state(state)

        # ---- independent cross-check -------------------------------------
        when = reference
        state = propagate(element, when, timescale=timescale)
        native = native_sgp4_state(element, when)
        jd_ut1 = timescale.from_datetime(when).ut1
        independent_ecef = teme_to_ecef(native.teme_km, jd_ut1)
        teme_radius = math.dist((0.0, 0.0, 0.0), native.teme_km)
        teme_speed = math.dist((0.0, 0.0, 0.0), native.teme_velocity_km_s)

        print()
        print("--- Independent cross-check at the reference instant ---")
        print(
            "  sgp4 native TEME : "
            f"{native.teme_km[0]:+.4f}, {native.teme_km[1]:+.4f}, "
            f"{native.teme_km[2]:+.4f} km (error code {native.error_code})"
        )
        print(
            "  |TEME| vs |GCRS|: "
            f"{teme_radius:.6f} vs {state.radius_km:.6f} km "
            f"(difference {teme_radius - state.radius_km:+.2e} km)"
        )
        print(
            "  speed TEME/GCRS : "
            f"{teme_speed:.6f} vs {state.speed_km_s:.6f} km/s "
            f"(difference {teme_speed - state.speed_km_s:+.2e} km/s)"
        )
        print(
            "  hand-rolled TEME->ECEF vs ITRS: "
            f"{math.dist(independent_ecef, state.ecef_km) * 1000.0:.3f} m apart"
        )
        print(
            f"  sidereal GMST    : {gmst_degrees(jd_ut1):.6f} deg "
            "(rotation between inertial and Earth-fixed frames)"
        )

    print()
    print(RULE)
    print("Coordinate systems used above")
    print(RULE)
    print("  WGS-84 geodetic : lat/lon/height on the WGS-84 ellipsoid (GPS datum)")
    print("  ITRS / ECEF     : Earth-fixed Cartesian, rotates with the Earth")
    print("  GCRS / ICRS     : geocentric inertial, axes fixed to the quasars")
    print("  TEME (sgp4 raw) : SGP4's native frame, cross-check only, not rendered")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


