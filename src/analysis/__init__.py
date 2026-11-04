"""Analysis layer for Orbix (Phase 2).

Two halves, deliberately separate:

* :mod:`src.analysis.provenance` and :mod:`src.analysis.frame` turn a loaded scene into
  one documented DataFrame - declared columns, stated units, and an explicit distinction
  between published elements, deterministic transforms and SGP4-propagated values.
* :mod:`src.analysis.stats` computes descriptive statistics, missing-data and freshness
  audits, robust outlier detection and within-group versus pooled correlations.

Neither half imports Streamlit, and neither fetches data: everything is derived from the
snapshot the globe is already showing.
"""

from __future__ import annotations

from src.analysis.frame import (
    GEO_BAND_KM,
    LEO_APOGEE_KM,
    MU_KM3_S2,
    REGIMES,
    WGS84_A_KM,
    catalog_frame,
    classify_regime,
    column_docs,
    validate_frame,
)
from src.analysis.provenance import (
    COLUMNS,
    ColumnKind,
    ColumnSpec,
    by_kind,
    column,
    kind_of,
)

__all__ = [
    "COLUMNS",
    "MU_KM3_S2",
    "WGS84_A_KM",
    "ColumnKind",
    "ColumnSpec",
    "by_kind",
    "catalog_frame",
    "column",
    "column_docs",
    "kind_of",
    "validate_frame",
]
