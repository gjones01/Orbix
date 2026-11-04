"""Column provenance registry for the analysis frame.

The most dangerous mistake in orbital data analysis is treating a *modelled* quantity as
an *observed* one - most commonly, reading altitude as if it were an orbital element, or
computing ``semi_major_axis - Earth_radius`` and calling it altitude. This registry makes
that distinction structural: every column in the analysis frame is declared here with its
provenance class, unit and source, and the frame builder validates itself against it
rather than relying on memory.

Provenance classes
------------------
``identity``    Names and catalogue numbers. Not measurements.
``grouping``    CelesTrak group membership (an editorial classification).
``element``     Published by CelesTrak/18 SDS in the OMM: a *fitted mean element*,
                observed by the Space Surveillance Network, not by Orbix.
``derived``     An exact algebraic transform of elements (e.g. period = 1440/n). No
                propagation model is involved, but it is still a *mean*-element quantity:
                ``semi_major_axis_km`` comes from the Kozai mean motion.
``propagated``  Output of the SGP4 model at one stated instant. Time-dependent: it changes
                along the orbit, so it must always be accompanied by that instant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["COLUMNS", "ColumnKind", "ColumnSpec", "by_kind", "column", "kind_of"]


class ColumnKind(str, Enum):
    """Where a column's values come from."""

    IDENTITY = "identity"
    GROUPING = "grouping"
    ELEMENT = "element"
    DERIVED = "derived"
    PROPAGATED = "propagated"


@dataclass(frozen=True)
class ColumnSpec:
    """Documentation for one analysis-frame column."""

    name: str
    label: str
    unit: str
    kind: ColumnKind
    source: str
    note: str = ""


#: Every column :func:`src.analysis.frame.catalog_frame` produces, in frame order.
COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("norad_id", "NORAD catalog ID", "integer", ColumnKind.IDENTITY,
               "OMM NORAD_CAT_ID", "Primary object identifier; unique within a frame."),
    ColumnSpec("name", "Object name", "text", ColumnKind.IDENTITY, "OMM OBJECT_NAME"),
    ColumnSpec("intl_designator", "International designator", "text",
               ColumnKind.IDENTITY, "OMM OBJECT_ID", "Launch designator, e.g. 1998-067A."),
    ColumnSpec("constellation", "Constellation", "category", ColumnKind.GROUPING,
               "primary CelesTrak group",
               "Editorial classification, not a physical property."),
    ColumnSpec("groups", "All groups", "text", ColumnKind.GROUPING,
               "CelesTrak group membership",
               "An object can belong to several groups; rendered once, counted once."),
    ColumnSpec("epoch", "Element epoch", "UTC", ColumnKind.ELEMENT, "OMM EPOCH",
               "When the orbit was fitted. Not the observation time of a position."),
    ColumnSpec("element_age_hours", "Element age", "h", ColumnKind.DERIVED,
               "scene anchor - epoch",
               "Age of the element set at the scene anchor (see frame attribute)."),
    ColumnSpec("mean_motion_rev_day", "Mean motion", "rev/day", ColumnKind.ELEMENT,
               "OMM MEAN_MOTION"),
    ColumnSpec("eccentricity", "Eccentricity", "dimensionless", ColumnKind.ELEMENT,
               "OMM ECCENTRICITY", "Dimensionless shape; 0 is a circle."),
    ColumnSpec("inclination_deg", "Inclination", "deg", ColumnKind.ELEMENT,
               "OMM INCLINATION"),
    ColumnSpec("raan_deg", "RAAN", "deg", ColumnKind.ELEMENT, "OMM RA_OF_ASC_NODE",
               "Right ascension of the ascending node: the orbital plane's orientation."),
    ColumnSpec("arg_pericenter_deg", "Argument of pericentre", "deg",
               ColumnKind.ELEMENT, "OMM ARG_OF_PERICENTER"),
    ColumnSpec("mean_anomaly_deg", "Mean anomaly", "deg", ColumnKind.ELEMENT,
               "OMM MEAN_ANOMALY"),
    ColumnSpec("bstar", "B* drag term", "1 / Earth radii", ColumnKind.ELEMENT,
               "OMM BSTAR",
               "A fitted drag coefficient, not a measured area or mass."),
    ColumnSpec("mean_motion_dot", "Mean motion derivative", "rev/day^2",
               ColumnKind.ELEMENT, "OMM MEAN_MOTION_DOT"),
    ColumnSpec("rev_at_epoch", "Revolutions at epoch", "count", ColumnKind.ELEMENT,
               "OMM REV_AT_EPOCH"),
    ColumnSpec("element_set_no", "Element set number", "count", ColumnKind.ELEMENT,
               "OMM ELEMENT_SET_NO"),
    ColumnSpec("period_min", "Orbital period", "min", ColumnKind.DERIVED,
               "1440 / mean_motion",
               "Exact algebra on the mean element: NOT a propagated quantity."),
    ColumnSpec("semi_major_axis_km", "Semi-major axis", "km", ColumnKind.DERIVED,
               "(mu / n^2)^(1/3), mu = 398600.4418 km^3/s^2",
               "From the Kozai mean motion, so it is a mean-element value. This is NOT "
               "altitude: it is measured from the Earth's centre and ignores "
               "eccentricity and the ellipsoid."),
    ColumnSpec("perigee_radius_km", "Perigee radius", "km", ColumnKind.DERIVED,
               "a (1 - e)", "Geocentric radius. Deliberately not called altitude."),
    ColumnSpec("apogee_radius_km", "Apogee radius", "km", ColumnKind.DERIVED,
               "a (1 + e)", "Geocentric radius. Deliberately not called altitude."),
    ColumnSpec("regime", "Orbital regime", "category", ColumnKind.DERIVED,
               "classified from perigee/apogee radius and eccentricity",
               "A CONVENTION with fixed km boundaries (see frame.GEO_BAND_KM), not a "
               "physical law. Derived from mean elements, so an eccentric object can sit "
               "outside its own regime at the anchor instant - compare with altitude_km "
               "before trusting either."),
    ColumnSpec("altitude_km", "Altitude", "km", ColumnKind.PROPAGATED,
               "SGP4 -> ITRS -> WGS-84 geodetic height at the anchor instant",
               "Time-dependent: where the satellite was at the anchor, above the "
               "ellipsoid. Differs from a(1+-e) - R_earth."),
    ColumnSpec("latitude_deg", "Sub-satellite latitude", "deg", ColumnKind.PROPAGATED,
               "SGP4 -> ITRS -> WGS-84 geodetic", "At the anchor instant."),
    ColumnSpec("longitude_deg", "Sub-satellite longitude", "deg",
               ColumnKind.PROPAGATED, "SGP4 -> ITRS -> WGS-84 geodetic",
               "At the anchor instant."),
    # Deliberately absent: instantaneous speed. BatchSample carries positions only, and a
    # finite difference across ~30 s keyframes is a chord average, not a speed. Reporting
    # it would be inventing a number.
)

_BY_NAME = {spec.name: spec for spec in COLUMNS}


def column(name: str) -> ColumnSpec:
    """Look up one column's documentation."""
    try:
        return _BY_NAME[name]
    except KeyError as exc:  # pragma: no cover - developer error
        raise KeyError(f"Undocumented analysis column: {name}") from exc


def kind_of(name: str) -> ColumnKind:
    """Provenance class of one column."""
    return column(name).kind


def by_kind(kind: ColumnKind) -> tuple[ColumnSpec, ...]:
    """All columns of a given provenance class, in frame order."""
    return tuple(spec for spec in COLUMNS if spec.kind is kind)
