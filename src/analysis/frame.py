"""The analysis DataFrame: one documented row per satellite in the loaded snapshot.

Design rules
------------
* **Same snapshot as the globe.** A frame is built from a ``ConstellationScene``, so the
  analysis tab and the globe always describe the same objects, elements and anchor
  instant. No second fetch, no second choice of groups.
* **One row per object, not per group.** An object in two groups appears once, with
  ``constellation`` = primary group and ``groups`` listing all memberships. This matches
  what the globe draws.
* **Documented columns only.** Every column is declared in
  :mod:`src.analysis.provenance`, and :func:`validate_frame` fails loudly if the frame
  and the registry disagree.
* **No silent imputation.** Missing element fields stay ``NaN``; the audit reports them
  instead of filling them with zero, which would look like a real measurement.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

from src.analysis.provenance import COLUMNS
from src.ui.presentation import ConstellationScene

__all__ = [
    "MU_KM3_S2",
    "NULLABLE_COLUMNS",
    "WGS84_A_KM",
    "catalog_frame",
    "column_docs",
    "validate_frame",
]

#: WGS-84 gravitational parameter of the Earth (km^3/s^2), used only to turn a mean
#: motion into a semi-major axis.
MU_KM3_S2 = 398_600.4418

#: WGS-84 equatorial radius (km). Documented so that geocentric radius and geodetic
#: altitude can be compared honestly; Orbix never subtracts it to fabricate altitude.
WGS84_A_KM = 6378.137

_TWO_PI = 2.0 * math.pi
_SECONDS_PER_DAY = 86_400.0

#: Columns that may legitimately be absent from the source OMM.
NULLABLE_COLUMNS = (
    "intl_designator",
    "bstar",
    "mean_motion_dot",
    "rev_at_epoch",
    "element_set_no",
)


def _semi_major_axis_km(mean_motion_rev_day: float) -> float:
    """Semi-major axis from the Kozai mean motion (mean elements, no propagation)."""
    if not mean_motion_rev_day or mean_motion_rev_day <= 0:
        return float("nan")
    n_rad_s = mean_motion_rev_day * _TWO_PI / _SECONDS_PER_DAY
    return (MU_KM3_S2 / (n_rad_s * n_rad_s)) ** (1.0 / 3.0)


def column_docs() -> pd.DataFrame:
    """The provenance registry as a table, for display and for tests."""
    return pd.DataFrame(
        [
            {
                "column": spec.name,
                "label": spec.label,
                "unit": spec.unit,
                "provenance": spec.kind.value,
                "source": spec.source,
                "note": spec.note,
            }
            for spec in COLUMNS
        ]
    )


def catalog_frame(scene: ConstellationScene) -> pd.DataFrame:
    """Build the analysis frame for a loaded scene.

    Parameters
    ----------
    scene:
        A scene from :func:`src.ui.presentation.load_constellation`. Its
        ``sample.times[0]`` is the anchor instant for every propagated column and the
        reference for ``element_age_hours``.

    Returns
    -------
    pandas.DataFrame
        One row per unique object, columns in :data:`src.analysis.provenance.COLUMNS`
        order, with ``.attrs["anchor"]`` set to the anchor instant, plus
        ``.attrs["provenance"]`` and ``.attrs["units"]``.
    """
    sample = scene.sample
    anchor = sample.times[0]
    position_of = {int(n): k for k, n in enumerate(sample.norad_ids)}

    rows: list[dict[str, object]] = []
    for norad_id in sorted(scene.catalog.satellites):
        satellite = scene.catalog.satellites[norad_id]
        element = satellite.element
        period = element.period_minutes
        axis = _semi_major_axis_km(element.mean_motion)
        eccentricity = float(element.eccentricity)

        row: dict[str, object] = {
            "norad_id": int(norad_id),
            "name": element.object_name,
            # An empty international designator is not an identifier: store it as null so
            # the missing-data audit can see it.
            "intl_designator": element.object_id or None,
            "constellation": satellite.primary_group,
            "groups": ",".join(sorted(satellite.groups)),
            "epoch": element.epoch,
            "element_age_hours": element.age_at(anchor).total_seconds() / 3600.0,
            "mean_motion_rev_day": float(element.mean_motion),
            "eccentricity": eccentricity,
            "inclination_deg": float(element.inclination),
            "raan_deg": float(element.ra_of_asc_node),
            "arg_pericenter_deg": float(element.arg_of_pericenter),
            "mean_anomaly_deg": float(element.mean_anomaly),
            "bstar": float(element.bstar) if element.bstar is not None else np.nan,
            "mean_motion_dot": (
                float(element.mean_motion_dot)
                if element.mean_motion_dot is not None
                else np.nan
            ),
            "rev_at_epoch": (
                float(element.rev_at_epoch) if element.rev_at_epoch is not None else np.nan
            ),
            "element_set_no": (
                float(element.element_set_no)
                if element.element_set_no is not None
                else np.nan
            ),
            "period_min": float(period) if period is not None else np.nan,
            "semi_major_axis_km": axis,
            "perigee_radius_km": axis * (1.0 - eccentricity),
            "apogee_radius_km": axis * (1.0 + eccentricity),
            # Propagated: valid only at the anchor instant.
            "altitude_km": np.nan,
            "latitude_deg": np.nan,
            "longitude_deg": np.nan,
        }

        k = position_of.get(int(norad_id))
        if k is not None and bool(sample.valid[k, 0]):
            row["altitude_km"] = float(sample.alt[k, 0])
            row["latitude_deg"] = float(sample.lat[k, 0])
            row["longitude_deg"] = float(sample.lng[k, 0])

        rows.append(row)

    frame = pd.DataFrame(rows, columns=[spec.name for spec in COLUMNS])
    frame = frame.astype({
        "norad_id": "int64",
        "name": "string",
        "intl_designator": "string",
        "constellation": "category",
        "groups": "string",
        "period_min": "float64",
        "rev_at_epoch": "float64",
        "element_set_no": "float64",
    })
    frame.attrs["anchor"] = anchor
    frame.attrs["window_minutes"] = scene.plan.duration_seconds / 60.0
    frame.attrs["provenance"] = {spec.name: spec.kind.value for spec in COLUMNS}
    frame.attrs["units"] = {spec.name: spec.unit for spec in COLUMNS}
    return frame


def validate_frame(
    frame: pd.DataFrame, *, nullable: Sequence[str] = NULLABLE_COLUMNS
) -> None:
    """Raise ``ValueError`` if the frame does not match the documented schema.

    Checks what would silently corrupt an analysis: an undocumented or missing column, a
    duplicate object, a null in a non-nullable column, or impossible element values.
    """
    expected = [spec.name for spec in COLUMNS]
    if list(frame.columns) != expected:
        raise ValueError(
            "frame columns do not match the provenance registry; "
            f"missing={sorted(set(expected) - set(frame.columns))} "
            f"unexpected={sorted(set(frame.columns) - set(expected))}"
        )
    if frame.empty:
        raise ValueError("empty frame")
    if frame["norad_id"].duplicated().any():
        raise ValueError("frame contains duplicate NORAD IDs")
    for name in expected:
        # altitude_km is null only where SGP4 could not produce a position.
        if name in nullable or name == "altitude_km":
            continue
        if frame[name].isna().any():
            raise ValueError(f"column {name!r} contains nulls but is not nullable")
    if (frame["eccentricity"] < 0).any() or (frame["eccentricity"] >= 1).any():
        raise ValueError("eccentricity outside [0, 1)")
    if (frame["mean_motion_rev_day"] <= 0).any():
        raise ValueError("non-positive mean motion")
