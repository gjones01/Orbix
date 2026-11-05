"""Scientific and filtering contracts for the approachable dashboard."""
import numpy as np
import pandas as pd
import pytest

from src.analysis import figures, stats
from src.analysis.overview import BANDS, KM_PER_MILE, altitude_subset, group_altitudes, overview


def sample():
    return pd.DataFrame({
        "norad_id": range(10), "name": [f"Satellite {n}" for n in range(10)],
        "constellation": pd.Categorical(["a"] * 5 + ["b"] * 5, categories=["a", "b", "unused"]),
        "regime": ["LEO"] * 5 + ["MEO"] * 5,
        "altitude_km": [400, 401, 402, 403, 1000, 2000, 2100, 2200, 30000, np.nan],
    })


def test_summary_counts_unique_rows_and_preserves_stratified_rules():
    frame = sample()
    result = overview(frame)
    _, expected = stats.outliers(stats.add_stratum(frame), "altitude_km", by="stratum")
    assert result["total"] == 10
    assert result["valid_altitudes"] == result["assessed"] == 9
    assert result["median_altitude_km"] == 1000
    assert result["counts"]["n"].sum() == 10
    assert "unused" not in result["counts"]["group"].tolist()
    pd.testing.assert_frame_equal(result["flagged"], expected)
    assert frame["altitude_km"].isna().sum() == 1


def test_height_bands_partition_valid_altitudes_including_boundaries():
    frame = sample()
    pieces = [altitude_subset(frame, band) for band in BANDS[1:]]
    ids = pd.concat(pieces)["norad_id"]
    assert len(ids) == ids.nunique() == 9
    assert 5 in pieces[1]["norad_id"].tolist()  # exactly 2000
    assert 8 in pieces[2]["norad_id"].tolist()  # exactly 30000


def test_miles_change_only_display_and_keep_counts_and_quartiles():
    summary = group_altitudes(sample())
    km = figures.altitude_ranges(summary, labels={}, colours={}, unit="km")
    mi = figures.altitude_ranges(summary, labels={}, colours={}, unit="mi")
    for a, b in zip(km.data, mi.data):
        assert np.asarray(b.x) == pytest.approx(np.asarray(a.x) / KM_PER_MILE)
    assert mi.layout.xaxis.type == "linear"
    assert "mi" in mi.layout.xaxis.title.text
    assert mi.data[2].customdata[0][0] == km.data[2].customdata[0][0]


def test_empty_missing_and_tiny_selections_are_explicit():
    frame = sample().iloc[:2].copy()
    frame["altitude_km"] = np.nan
    result = overview(frame)
    assert np.isnan(result["median_altitude_km"])
    assert result["assessed"] == 0
    assert result["flagged"].empty
    fig = figures.altitude_ranges(group_altitudes(frame), labels={}, colours={})
    assert not fig.data and fig.layout.annotations
    empty = overview(frame.iloc[:0])
    assert empty["total"] == 0 and empty["largest"] is None
