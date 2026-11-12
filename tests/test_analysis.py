"""Tests for the Phase 2 analysis layer: frame schema, provenance and statistics.

Two kinds of test are used deliberately:

* **Real data** (the committed snapshots behind the ``scene`` fixture) to prove the frame
  really describes the same objects as the globe, and that the derived quantities agree
  with the elements to floating-point tolerance.
* **Hand-computed synthetic frames** for the statistics, because a test that only checks
  "the function returned a number" cannot detect a wrong quartile or a mis-signed
  correlation. Every expected value below is worked out in the comment beside it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis import stats
from src.analysis.frame import (
    MU_KM3_S2,
    REGIMES,
    WGS84_A_KM,
    catalog_frame,
    classify_regime,
    column_docs,
    validate_frame,
)
from src.analysis.provenance import COLUMNS, ColumnKind, by_kind, kind_of


@pytest.fixture(scope="module")
def scene():
    """The same offline scene the globe uses: stations plus GPS."""
    from src.ui.presentation import load_constellation

    return load_constellation(["stations", "gps-ops"], window_minutes=20, offline=True)


@pytest.fixture(scope="module")
def frame(scene):
    return catalog_frame(scene)


def synthetic(**values: list[float]) -> pd.DataFrame:
    """A small frame that satisfies the columns the statistics functions touch."""
    n = len(next(iter(values.values())))
    frame = pd.DataFrame(values)
    frame["norad_id"] = np.arange(1, n + 1, dtype="int64")
    frame["name"] = [f"OBJ{index}" for index in range(n)]
    if "constellation" not in frame.columns:
        frame["constellation"] = "A"
    if "regime" not in frame.columns:
        frame["regime"] = "LEO"
    return frame


# --------------------------------------------------------------------------- frame


def test_frame_schema_matches_the_provenance_registry(frame, scene):
    validate_frame(frame)  # raises if the schema and the registry disagree
    assert list(frame.columns) == [spec.name for spec in COLUMNS]
    assert len(frame) == scene.unique_count
    assert frame["norad_id"].is_unique
    assert set(frame["constellation"].astype(str)) >= {"stations", "gps-ops"}


def test_the_three_provenance_classes_stay_separated():
    """An element, a derived quantity and a propagated value are not interchangeable."""
    assert kind_of("inclination_deg") is ColumnKind.ELEMENT
    assert kind_of("semi_major_axis_km") is ColumnKind.DERIVED
    assert kind_of("altitude_km") is ColumnKind.PROPAGATED
    assert {spec.name for spec in by_kind(ColumnKind.PROPAGATED)} == {
        "altitude_km", "latitude_deg", "longitude_deg"
    }
    # Every column that reaches the frame is documented, and every documented column is
    # in the frame - so no undocumented number can slip into the analysis.
    documented = set(column_docs()["column"])
    assert documented == {spec.name for spec in COLUMNS}


def test_derived_quantities_follow_from_the_elements(frame):
    """Recompute period and semi-major axis independently and compare."""
    n = frame["mean_motion_rev_day"].to_numpy()
    period = 1440.0 / n
    axis = (MU_KM3_S2 / (n * 2.0 * np.pi / 86_400.0) ** 2) ** (1.0 / 3.0)

    assert np.allclose(frame["period_min"], period, rtol=0, atol=1e-9)
    assert np.allclose(frame["semi_major_axis_km"], axis, rtol=0, atol=1e-6)
    assert (frame["perigee_radius_km"] < frame["apogee_radius_km"]).all()
    eccentricity = frame["eccentricity"]
    assert np.allclose(frame["perigee_radius_km"],
                       frame["semi_major_axis_km"] * (1 - eccentricity), atol=1e-9)
    assert np.allclose(frame["apogee_radius_km"],
                       frame["semi_major_axis_km"] * (1 + eccentricity), atol=1e-9)

    iss = frame.loc[frame["norad_id"] == 25544]
    if not iss.empty:
        assert 90.0 < float(iss["period_min"].iloc[0]) < 95.0


def test_altitude_is_not_semi_major_axis_minus_earth_radius(frame, scene):
    """The distinction the provenance registry exists to protect.

    ``a - R_earth`` ignores eccentricity and the ellipsoid, so it approximates altitude only
    for a circular orbit. Compare at each element's epoch: its mean a/e do not bound
    a later SGP4 orbit after atmospheric drag has changed it. In particular, low
    HMU-SAT2 elements descend substantially within a few days. The epoch slack
    allows ``a*e`` for eccentricity plus 21 km for ellipsoid/mean-element effects.
    Missing propagated positions must remain missing, not become a false altitude.
    """
    from src.orbital.propagation import propagate

    epoch_altitudes = frame["norad_id"].map(
        lambda n: propagate(scene.element(n), scene.element(n).epoch).altitude_km
    )
    gap = ((frame["semi_major_axis_km"] - WGS84_A_KM) - epoch_altitudes).abs()
    slack = frame["semi_major_axis_km"] * frame["eccentricity"]

    assert bool((gap <= slack + 21.0).all())            # epoch bound, every row
    near_circular = frame.loc[frame["eccentricity"] < 1e-3].index
    assert gap.loc[near_circular].max() < 25.0
    assert gap.max() > 100.0
    actual_gap = ((frame["semi_major_axis_km"] - WGS84_A_KM) - frame["altitude_km"]).abs()
    assert actual_gap.dropna().max() > 100.0


def test_validate_frame_rejects_duplicates_and_undocumented_columns(frame):
    with pytest.raises(ValueError, match="duplicate"):
        validate_frame(pd.concat([frame, frame.iloc[[0]]], ignore_index=True))
    with pytest.raises(ValueError, match="registry"):
        validate_frame(frame.drop(columns=["bstar"]))


def test_frame_reports_the_same_objects_as_the_globe(frame, scene):
    assert frame.attrs["anchor"] == scene.sample.times[0]
    assert set(int(v) for v in frame["norad_id"]) == set(scene.catalog.satellites)
    invalid = int((~scene.sample.valid[:, 0]).sum())
    assert int(frame["altitude_km"].isna().sum()) == invalid


# ------------------------------------------------------------------ statistics


def test_a_bimodal_group_is_flagged_as_heterogeneous_not_contaminated():
    """A minority high-altitude mode is flagged as heterogeneity, not as bad objects.

    The real ``weather`` group mixes ~800 km polar platforms with GEO meteorological
    satellites: median 830 km, max 35 800 km, and 23 of 74 objects flagged. Emulating that
    as 60 low objects (800..859 km) plus 15 GEO ones puts q3 inside the low cluster, so all
    15 GEO objects fall outside the fence (20% flagged) - which is evidence about the group,
    not about those 15 satellites. This is why ``heterogeneous`` exists.
    """
    frame = synthetic(altitude_km=[800.0 + i for i in range(60)] + [35_786.0] * 15)
    summary, flagged = stats.outliers(frame, "altitude_km")
    row = summary.iloc[0]

    assert row["outlier_pct"] == pytest.approx(20.0)
    assert bool(row["heterogeneous"]) is True
    assert "multi-modal" in row["note"]
    assert len(flagged) == 15


def test_an_even_two_point_mixture_is_not_flagged_and_that_is_correct():
    """An exact 50/50 mixture at two values is *not* flagged - and that is not a bug.

    With half the data on each mode, q1 and q3 land on the modes themselves, so IQR = 17493
    and the fence spans both clusters; the MAD is equally large, so the modified z-score is
    ~0 too. Robust location/scale cannot see a bimodality that accounts for half the data.
    It is a reminder that these flags detect contamination near a single mode, and that
    distributions must be plotted - not just summarised.
    """
    frame = synthetic(altitude_km=[800.0] * 10 + [35_786.0] * 10)
    summary, flagged = stats.outliers(frame, "altitude_km")
    row = summary.iloc[0]

    assert row["iqr"] == pytest.approx(34_986.0)
    assert row["n_flagged"] == 0
    assert flagged.empty
    assert bool(row["heterogeneous"]) is False


def test_missing_report_never_imputes():
    """A missing B* is reported as missing, not silently replaced by zero."""
    frame = synthetic(altitude_km=[100.0, np.nan, 300.0], bstar=[1e-4, np.nan, 2e-4])
    report = stats.missing_report(frame).set_index("column")

    assert int(report.loc["altitude_km", "missing"]) == 1
    assert report.loc["altitude_km", "provenance"] == "propagated"
    assert report.loc["bstar", "missing_pct"] == pytest.approx(100.0 / 3.0)
    # The value itself is still absent: nothing was filled in.
    assert frame["bstar"].isna().sum() == 1


def test_describe_matches_hand_computed_quartiles():
    """Values 1..10: median 5.5, q1 3.25, q3 7.75, IQR 4.5 (pandas linear quantiles)."""
    frame = synthetic(altitude_km=[float(v) for v in range(1, 11)])
    described = stats.describe(frame, ["altitude_km"]).iloc[0]

    assert described["n"] == 10
    assert described["missing"] == 0
    assert described["mean"] == pytest.approx(5.5)
    assert described["median"] == pytest.approx(5.5)
    assert described["q1"] == pytest.approx(3.25)
    assert described["q3"] == pytest.approx(7.75)
    assert described["iqr"] == pytest.approx(4.5)
    assert described["unit"] == "km"


def test_outliers_flags_the_object_outside_the_tukey_fence():
    """Values 100 (x19) and 500 (x1).

    q1 = q3 = 100, so IQR = 0 and the fence is [100, 100]: the 500 is outside it. Exactly
    one object is flagged, and it is named.
    """
    frame = synthetic(altitude_km=[100.0] * 19 + [500.0])
    summary, flagged = stats.outliers(frame, "altitude_km")

    row = summary.iloc[0]
    assert row["n"] == 20
    assert row["iqr"] == pytest.approx(0.0)
    assert row["n_flagged"] == 1
    assert bool(row["degenerate"]) is True          # MAD is zero here too
    assert "IQR fence only" in row["note"]

    assert len(flagged) == 1
    assert flagged.iloc[0]["value"] == pytest.approx(500.0)
    assert flagged.iloc[0]["norad_id"] == 20
    assert "IQR fence" in flagged.iloc[0]["reason"]


def test_outliers_is_honest_when_mad_is_zero():
    """A group where more than half share one value must not report infinite z-scores."""
    frame = synthetic(altitude_km=[5.0, 5.0, 5.0, 5.0, 5.0, 9.0])
    summary, flagged = stats.outliers(frame, "altitude_km")

    row = summary.iloc[0]
    assert bool(row["degenerate"]) is True
    assert row["mad"] == pytest.approx(0.0)
    assert "MAD is zero" in row["note"]
    assert row["n_flagged"] == 1
    assert np.isnan(flagged.iloc[0]["robust_z"])       # undefined, not infinity


def test_robust_z_flags_a_value_the_iqr_fence_misses():
    """Values 1..40 plus 60: the IQR fence stops at 61, so only the z-score fires.

    With 41 values, q1 = 11, q3 = 31, IQR = 20, upper fence = 61 -> 60 is inside, and
    is caught by the modified z-score (median 21, MAD 10 -> z ~ 2.63) when the
    threshold is lowered to 2.5. This proves the two criteria are independent.
    """
    frame = synthetic(altitude_km=[float(v) for v in range(1, 41)] + [60.0])
    summary, flagged = stats.outliers(frame, "altitude_km", mad_z=2.5)

    assert "modified z" in flagged.iloc[0]["reason"]
    assert summary.iloc[0]["n_iqr"] == 0
    assert summary.iloc[0]["n_mad"] >= 1


def test_pooled_correlation_hides_a_sign_flip():
    """Simpson-type aggregation bias, worked out by hand.

    Group A: x 1..5, y 10..6 -> rho = -1. Group B: x 11..15, y 20..16 -> rho = -1.
    Pooled, the x ranks are 1..10 and the y ranks are 5,4,3,2,1,10,9,8,7,6, whose mean
    products give a *positive* rank covariance (+42.5/10), so pooled rho > 0 while every
    group is perfectly negative. That is the trap the analysis must not fall into.
    """
    group_a = synthetic(altitude_km=[1, 2, 3, 4, 5], inclination_deg=[10, 9, 8, 7, 6])
    group_b = synthetic(altitude_km=[11, 12, 13, 14, 15],
                        inclination_deg=[20, 19, 18, 17, 16])
    group_b["constellation"] = "B"
    frame = pd.concat([group_a, group_b], ignore_index=True)

    comparison = stats.pooled_vs_within(
        frame, ["altitude_km", "inclination_deg"], min_n=5
    )
    row = comparison.iloc[0]

    assert row["within_median"] == pytest.approx(-1.0)
    assert row["pooled_rho"] > 0.2
    # Every group is negative while the pooled coefficient is positive: this is the narrow
    # condition that earns the word "reversal", and it is reported as such.
    assert row["same_sign_share"] == pytest.approx(0.0)
    assert row["reversed_groups"] == 2
    assert bool(row["simpson_pattern"]) is True
    assert row["gap"] == pytest.approx(row["pooled_rho"] + 1.0)


def test_a_magnitude_gap_without_sign_change_is_not_a_reversal():
    """The audit requested: a large difference in strength is NOT a sign reversal.

    Each group is the same shape: y rises with x within half the range and resets, so the
    within-group coefficient is a modest positive number. Group B then repeats that shape
    at a much higher y, so pooling produces a strong positive coefficient that no individual
    group shows. Direction never disagrees - every group is positive, the pooled value is
    positive - but the magnitudes differ by a lot. The report must call that a material
    difference and must NOT claim a reversal or Simpson's paradox.
    """
    shape = list(range(10)) * 2
    group_a = synthetic(altitude_km=list(range(20)), inclination_deg=shape)
    group_b = synthetic(altitude_km=list(range(100, 120)),
                        inclination_deg=[100 + value for value in shape])
    group_b["constellation"] = "B"
    frame = pd.concat([group_a, group_b], ignore_index=True)

    row = stats.pooled_vs_within(
        frame, ["altitude_km", "inclination_deg"], min_n=10
    ).iloc[0]

    assert row["within_median"] > 0.2                     # same, positive direction
    assert row["pooled_rho"] > row["within_median"]
    assert row["same_sign_share"] == pytest.approx(1.0)    # no group disagrees
    assert row["reversed_groups"] == 0
    assert bool(row["simpson_pattern"]) is False           # no reversal claimed
    assert bool(row["material_difference"]) is True        # but the sizes differ


def test_correlation_is_undefined_for_a_constant_group():
    """Inclination inside one Starlink shell has no variance, so rho does not exist."""
    group_a = synthetic(altitude_km=[1.0, 2.0, 3.0, 4.0],
                        inclination_deg=[1.0, 2.0, 3.0, 4.0])
    group_b = synthetic(altitude_km=[5.0, 6.0, 7.0, 8.0], inclination_deg=[53.0] * 4)
    group_b["constellation"] = "B"
    frame = pd.concat([group_a, group_b], ignore_index=True)

    comparison = stats.pooled_vs_within(
        frame, ["altitude_km", "inclination_deg"], min_n=4
    )
    row = comparison.iloc[0]

    assert row["n_groups_defined"] == 1         # only group A is defined
    assert row["undefined_groups"] == 1         # and group B is reported, not hidden
    matrix = stats.correlation_matrix(
        frame, ["inclination_deg", "altitude_km"], by="B"
    )
    assert np.isnan(matrix.iloc[0, 1])


def test_composition_reports_the_dominant_group_share():
    frame = synthetic(altitude_km=[1.0] * 10)
    frame["constellation"] = ["A"] * 9 + ["B"]
    composition = stats.composition(frame).set_index("group")

    assert int(composition.loc["A", "n"]) == 9
    assert composition.loc["A", "share_pct"] == pytest.approx(90.0)
    assert composition["share_pct"].sum() == pytest.approx(100.0)


def test_freshness_identifies_the_oldest_element_set():
    frame = synthetic(altitude_km=[1.0, 2.0, 3.0])
    frame["element_age_hours"] = [1.0, 2.0, 100.0]
    frame.loc[2, "name"] = "ANCIENT"
    report = stats.freshness(frame).iloc[0]

    assert report["median_age_h"] == pytest.approx(2.0)
    assert report["max_age_h"] == pytest.approx(100.0)
    assert int(report["stale_count"]) == 1          # 100 h > 84 h
    assert report["oldest_name"] == "ANCIENT"


# --------------------------------------------------------------- regimes


def test_regime_boundaries_are_applied_in_the_documented_order():
    """Fixed km boundaries, checked at the edges and for the eccentric overrides."""
    assert classify_regime(6778.0, 6778.0, 0.0004) == "LEO"       # ISS
    assert classify_regime(26500.0, 26600.0, 0.009) == "MEO"      # GPS
    assert classify_regime(42160.0, 42170.0, 0.0002) == "GEO"     # GEO weather
    assert classify_regime(6600.0, 46000.0, 0.74) == "HEO"        # GTO debris
    # e >= 0.25 wins even though the perigee is inside LEO: the whole orbit is not LEO.
    assert classify_regime(6700.0, 44000.0, 0.30) == "HEO"
    # Just outside the LEO ceiling and not in the GEO band -> the MEO bucket.
    assert classify_regime(8380.0, 8390.0, 0.001) == "MEO"
    assert classify_regime(float("nan"), 8000.0, 0.001) == "unknown"


def test_regime_is_a_documented_derived_column(frame):
    assert kind_of("regime") is ColumnKind.DERIVED
    assert set(frame["regime"].astype(str)) <= set(REGIMES) | {"unknown"}
    validate_frame(frame)      # regime must not introduce nulls


def test_regime_counts_exposes_a_mixed_group():
    """The weather case: one group, two populations, visible in a crosstab."""
    leo = synthetic(altitude_km=[800.0, 810.0])
    geo = synthetic(altitude_km=[35_786.0, 35_790.0])
    geo["regime"] = "GEO"
    frame = pd.concat([leo, geo], ignore_index=True)
    frame["constellation"] = "weather"

    table = stats.regime_counts(frame).set_index("constellation")
    assert int(table.loc["weather", "LEO"]) == 2
    assert int(table.loc["weather", "GEO"]) == 2


def test_add_stratum_preserves_every_row_and_column():
    """Stratification must not delete or rewrite the observations it groups."""
    leo = synthetic(altitude_km=[800.0, 810.0])
    geo = synthetic(altitude_km=[35_786.0, 35_790.0])
    geo["regime"] = "GEO"
    frame = pd.concat([leo, geo], ignore_index=True)
    frame["constellation"] = "weather"

    stratified = stats.add_stratum(frame)
    assert len(stratified) == len(frame)
    assert sorted(stratified["norad_id"]) == sorted(frame["norad_id"])
    assert set(stratified["stratum"]) == {"weather / LEO", "weather / GEO"}
    # The original frame is untouched.
    assert "stratum" not in frame.columns
    assert stats.add_stratum(frame, with_regime=False)["stratum"].unique().tolist() == [
        "weather"
    ]


def test_stratifying_stops_a_legitimate_population_being_called_outliers():
    """Requirement 1, as a test: stratify first, then the flags go away.

    Unstratified, a weather-like group (60 LEO + 15 GEO) flags the 15 GEO platforms as
    outliers and reports the group as heterogeneous. Stratified by constellation *and*
    regime, each stratum is internally consistent and the flags disappear. The objects were
    never the problem.
    """
    leo = synthetic(altitude_km=[800.0 + i for i in range(60)])
    geo = synthetic(altitude_km=[35_786.0] * 15)
    geo["regime"] = "GEO"
    frame = pd.concat([leo, geo], ignore_index=True)
    frame["constellation"] = "weather"

    pooled_summary, pooled_flagged = stats.outliers(frame, "altitude_km")
    assert pooled_summary.iloc[0]["n_flagged"] == 15
    assert bool(pooled_summary.iloc[0]["heterogeneous"]) is True

    stratified = stats.add_stratum(frame)
    summary, flagged = stats.outliers(stratified, "altitude_km", by="stratum")
    assert set(summary["group"]) == {"weather / LEO", "weather / GEO"}
    assert int(summary["n_flagged"].sum()) == 0
    assert flagged.empty
    assert not summary["heterogeneous"].any()


def test_heterogeneity_threshold_is_a_configurable_heuristic():
    """The same data, two thresholds, two honest reports - and no rows removed."""
    frame = synthetic(altitude_km=[800.0 + i for i in range(60)] + [35_786.0] * 15)

    _, flagged_at_50 = stats.outliers(frame, "altitude_km", heterogeneous_pct=50.0)
    summary_at_50, _ = stats.outliers(frame, "altitude_km", heterogeneous_pct=50.0)
    summary_at_5, _ = stats.outliers(frame, "altitude_km", heterogeneous_pct=5.0)

    assert bool(summary_at_50.iloc[0]["heterogeneous"]) is False   # 20% < 50%
    assert bool(summary_at_5.iloc[0]["heterogeneous"]) is True     # 20% > 5%
    # The observations are reported identically either way; only the interpretation moves.
    assert set(flagged_at_50["norad_id"]) == set(
        stats.outliers(frame, "altitude_km", heterogeneous_pct=5.0)[1]["norad_id"]
    )
    assert len(frame) == 75


def test_per_group_correlations_explains_each_missing_coefficient():
    """Undefined coefficients are reported with a reason, not dropped."""
    varying = synthetic(altitude_km=[1.0, 2.0, 3.0, 4.0],
                        inclination_deg=[1.0, 2.0, 3.0, 4.0])
    constant = synthetic(altitude_km=[1.0, 2.0, 3.0, 4.0], inclination_deg=[53.0] * 4)
    constant["constellation"] = "B"
    tiny = synthetic(altitude_km=[1.0, 2.0], inclination_deg=[1.0, 2.0])
    tiny["constellation"] = "C"
    frame = pd.concat([varying, constant, tiny], ignore_index=True)

    report = stats.per_group_correlations(
        frame, "altitude_km", "inclination_deg", min_n=3
    ).set_index("group")

    assert bool(report.loc["A", "defined"]) is True
    assert report.loc["A", "rho"] == pytest.approx(1.0)
    assert not report.loc["B", "defined"]
    assert "constant" in report.loc["B", "reason"]
    assert not report.loc["C", "defined"]
    assert "complete pairs" in report.loc["C", "reason"]


# ---------------------------------------------------------------------- real data


def test_all_analyses_run_on_a_real_snapshot(frame):
    """Smoke test with plausible bounds: catches schema drift the unit tests cannot."""
    described = stats.describe(frame)
    assert {"altitude_km", "inclination_deg", "eccentricity"} <= set(
        described["variable"]
    )
    assert not frame.loc[frame["altitude_km"] < 2000].empty
    assert (frame["altitude_km"].dropna() > -500).all()
    assert frame["inclination_deg"].between(0, 180).all()

    summary, _ = stats.outliers(frame, "altitude_km")
    assert len(summary) == frame["constellation"].nunique()

    comparison = stats.pooled_vs_within(
        frame, ["altitude_km", "inclination_deg", "eccentricity", "period_min"]
    )
    assert len(comparison) == 6            # 4 choose 2
    assert comparison["pooled_rho"].abs().le(1.0).all()


