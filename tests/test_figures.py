"""Tests for the Plotly figure builders and the chart -> globe selection bridge.

Figures are tested structurally (trace counts, axis titles, customdata) rather than
visually: the point is that a click can be mapped to the right NORAD id, that units are
declared, and that an empty filter degrades to a message instead of an exception.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis import figures, stats


@pytest.fixture(scope="module")
def frame():
    from src.analysis.frame import catalog_frame
    from src.ui.presentation import load_constellation

    scene = load_constellation(["stations", "gps-ops", "weather"], window_minutes=20,
                               offline=True)
    return catalog_frame(scene)


@pytest.fixture(scope="module")
def colours():
    return {"stations": "#58b6ff", "gps-ops": "#7ee0b8", "weather": "#ffb454"}


def test_regime_scatter_has_one_trace_per_constellation_and_regime(frame, colours):
    fig = figures.regime_scatter(frame, colours=colours)

    expected = frame.groupby(["constellation", "regime"], observed=True).ngroups
    assert len(fig.data) == expected
    # Colour carries the constellation: one legend entry per constellation, even though a
    # mixed constellation contributes several traces (one per regime/shape).
    assert sum(bool(trace.showlegend) for trace in fig.data) == \
        frame["constellation"].nunique()
    assert fig.layout.xaxis.title.text.startswith("Inclination")
    assert "km" in fig.layout.yaxis.title.text


def test_regime_scatter_carries_norad_ids_and_highlight_stays_on_top(frame, colours):
    fig = figures.regime_scatter(frame, colours=colours, highlight_ids=[25544])

    star = fig.data[-1]
    assert star.name == "highlighted"
    assert int(np.asarray(star.customdata, dtype=object)[0][0]) == 25544
    background = np.concatenate([
        np.asarray(trace.customdata, dtype=object)[:, 0]
        for trace in fig.data[:-1] if trace.customdata is not None
    ])
    assert 25544 not in background


def test_regime_scatter_switches_to_webgl_only_above_the_threshold(frame, colours):
    """Small selections stay SVG (crisper, selectable); large ones go to WebGL."""
    import plotly.graph_objects as go

    assert isinstance(figures.regime_scatter(frame, colours=colours).data[0],
                      go.Scatter)

    tiled = pd.concat(
        [frame.assign(norad_id=frame["norad_id"] + 100_000 * index)
         for index in range(40)],
        ignore_index=True,
    )
    assert len(tiled) > figures.GL_THRESHOLD
    assert isinstance(figures.regime_scatter(tiled, colours=colours).data[0],
                      go.Scattergl)


def test_every_figure_survives_an_empty_frame():
    empty = pd.DataFrame(columns=["norad_id", "name", "constellation", "regime",
                                  "altitude_km", "inclination_deg", "period_min"])

    for fig in (
        figures.regime_scatter(empty),
        figures.distribution_box(empty, "altitude_km"),
        figures.outlier_strip(empty, "altitude_km"),
        figures.correlation_heatmap(pd.DataFrame()),
        figures.pooled_within_chart(pd.DataFrame()),
        figures.freshness_bar(pd.DataFrame(), stale_hours=84.0),
        figures.missing_bar(pd.DataFrame()),
    ):
        assert len(fig.data) == 0
        assert fig.layout.annotations, "an empty figure must explain itself"


def test_distribution_violin_shows_density_and_box_shows_quartiles(frame, colours):
    box = figures.distribution_box(frame, "altitude_km", colours=colours)
    violin = figures.distribution_box(frame, "altitude_km", colours=colours,
                                      kind="violin")

    assert all(type(trace).__name__ == "Box" for trace in box.data)
    assert all(type(trace).__name__ == "Violin" for trace in violin.data)
    assert len(box.data) == frame["constellation"].nunique()


def test_outlier_strip_keeps_every_observation(frame):
    summary, _ = stats.outliers(frame, "altitude_km")
    fig = figures.outlier_strip(frame, "altitude_km", summary=summary)

    points = sum(len(trace.y) for trace in fig.data
                 if type(trace).__name__ == "Scattergl")
    assert points == int(frame["altitude_km"].notna().sum())
    assert any(shape.type == "line" for shape in fig.layout.shapes), \
        "fences must be drawn so the rule is visible"


def test_freshness_bar_marks_the_stale_threshold(frame):
    table = stats.freshness(frame)
    fig = figures.freshness_bar(table, stale_hours=stats.STALE_ELEMENT_HOURS)

    assert list(fig.data[0].x) == list(table["group"].astype(str))
    assert fig.data[0].error_y.array is not None
    assert any(shape.type == "line" for shape in fig.layout.shapes)


def test_missing_bar_says_so_when_nothing_is_missing(frame):
    fig = figures.missing_bar(stats.missing_report(frame))
    assert "No missing values" in fig.layout.annotations[0].text


def test_correlation_heatmap_spans_minus_one_to_plus_one(frame):
    matrix = stats.correlation_matrix(frame, ["altitude_km", "inclination_deg",
                                              "eccentricity"])
    fig = figures.correlation_heatmap(matrix)

    assert fig.data[0].z.shape == (3, 3)
    assert fig.data[0].zmin == -1 and fig.data[0].zmax == 1


def test_pooled_within_chart_adds_per_group_points_for_a_single_pair(frame):
    single = stats.pooled_vs_within(frame, ["altitude_km", "bstar"]).iloc[[0]]
    per_group = stats.per_group_correlations(
        frame, str(single.iloc[0]["x"]), str(single.iloc[0]["y"])
    )
    fig = figures.pooled_within_chart(single, per_group=per_group)

    names = [trace.name for trace in fig.data]
    assert "pooled" in names and "within-group median" in names
    assert "per group" in names


def test_selection_maps_back_to_norad_ids(frame, colours):
    fig = figures.regime_scatter(frame, colours=colours)
    customdata = np.asarray(fig.data[0].customdata, dtype=object)
    wanted = [int(customdata[0][0]), int(customdata[1][0])]

    state = {"selection": {"points": [
        {"curve_number": 0, "point_index": 0},
        {"curve_number": 0, "point_index": 1},
        {"curve_number": 0, "point_index": 1},        # duplicate
    ]}}
    assert figures.selection_norad_ids(fig, state) == sorted(set(wanted))


@pytest.mark.parametrize("state", [
    {},
    {"selection": {}},
    {"selection": {"points": []}},
    {"selection": {"points": [{"curve_number": 99, "point_index": 0}]}},
    {"selection": {"points": [{"curve_number": 0, "point_index": 10 ** 6}]}},
    {"selection": {"points": [{"curve_number": "x", "point_index": "y"}]}},
])
def test_unmappable_selections_degrade_to_no_highlight(state, frame, colours):
    """A selection that cannot be mapped must never highlight the wrong satellite."""
    fig = figures.regime_scatter(frame, colours=colours)
    assert figures.selection_norad_ids(fig, state) == []
