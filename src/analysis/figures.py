"""Plotly figure builders for the Analysis tab.

Pure Plotly: no Streamlit import, no data access. Each function takes the DataFrames
produced by :mod:`src.analysis.stats` and returns a ``go.Figure``, which makes the figures
testable without a browser and keeps the UI module free of chart code.

Conventions
-----------
* One trace per (constellation, regime) so a legend entry maps to a colour and a marker
  shape maps to a regime. ``legendgroup`` collapses the regimes under one constellation.
* Large collections use ``Scattergl`` (WebGL) above :data:`GL_THRESHOLD` points; the
  figure API is otherwise identical.
* Every figure carries the unit in its axis title, because a number without a unit is
  where orbital-data mistakes start.
* Empty input returns a figure with an explanatory annotation instead of an exception, so
  a filter that removes everything degrades to a message.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from src.analysis.provenance import column as column_spec

__all__ = [
    "GL_THRESHOLD",
    "INK",
    "REGIME_SYMBOLS",
    "regime_scatter",
    "distribution_box",
    "freshness_bar",
    "missing_bar",
    "outlier_strip",
    "correlation_heatmap",
    "pooled_within_chart",
    "selection_norad_ids",
]

#: Above this many points a scatter is drawn with WebGL.
GL_THRESHOLD = 2000

#: Marker shape per orbital regime, so shape carries meaning independently of colour.
REGIME_SYMBOLS: dict[str, str] = {
    "LEO": "circle",
    "MEO": "diamond",
    "GEO": "square",
    "HEO": "triangle-up",
    "unknown": "x",
}

_BG = "#04060b"
_PANEL = "rgba(11, 17, 27, 0.92)"
_GRID = "rgba(124, 141, 163, 0.18)"
INK = "#dbe6f2"
_MUTED = "#7c8da3"
_ACCENT = "#58b6ff"
_WARN = "#ffb454"
_BAD = "#ff6b6b"


def _empty(message: str, *, height: int = 340) -> go.Figure:
    """A figure that explains why it is empty rather than showing nothing."""
    fig = go.Figure()
    fig.add_annotation(
        text=message, showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        font={"color": _MUTED, "size": 12},
    )
    return _theme(fig, height=height)


def _theme(
    fig: go.Figure,
    *,
    height: int = 380,
    x_title: str = "",
    y_title: str = "",
    showlegend: bool = True,
) -> go.Figure:
    """Apply the Orbix dark theme to a figure."""
    fig.update_layout(
        height=height,
        paper_bgcolor=_BG,
        plot_bgcolor=_PANEL,
        font={"color": INK, "size": 12},
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        showlegend=showlegend,
        legend={
            "orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0,
            "font": {"size": 11}, "bgcolor": "rgba(0,0,0,0)",
        },
        hoverlabel={"bgcolor": "#0b111b", "font": {"color": INK, "size": 11}},
    )
    fig.update_xaxes(
        title={"text": x_title, "font": {"size": 11, "color": _MUTED}},
        gridcolor=_GRID, zerolinecolor=_GRID, linecolor=_GRID,
        tickfont={"size": 10, "color": _MUTED},
    )
    fig.update_yaxes(
        title={"text": y_title, "font": {"size": 11, "color": _MUTED}},
        gridcolor=_GRID, zerolinecolor=_GRID, linecolor=_GRID,
        tickfont={"size": 10, "color": _MUTED},
    )
    return fig


def _axis_title(name: str) -> str:
    spec = column_spec(name)
    return f"{spec.label} ({spec.unit})" if spec.unit else spec.label


def group_count_bar(counts: pd.DataFrame, *, labels: Mapping[str, str],
                    colours: Mapping[str, str]) -> go.Figure:
    """A zero-baseline count chart; one primary group per unique satellite."""
    if counts.empty:
        return _empty("Select a satellite group to compare.")
    fig = go.Figure(go.Bar(
        x=counts["n"], y=[labels.get(g, g) for g in counts["group"]],
        orientation="h", marker_color=[colours.get(g, _ACCENT) for g in counts["group"]],
        text=[f"{n:,}  ·  {share:.1f}%" for n, share in
              zip(counts["n"], counts["share_pct"])], textposition="outside",
        cliponaxis=False, customdata=counts[["group", "share_pct"]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>%{x:,} satellites<br>%{customdata[1]:.1f}% of selection<extra></extra>",
    ))
    _theme(fig, height=max(230, len(counts) * 48 + 65),
           x_title="Number of satellites", showlegend=False)
    fig.update_layout(margin=dict(l=10, r=100, t=10, b=10), bargap=0.48)
    fig.update_xaxes(rangemode="tozero", tickformat=",d")
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(size=13, color=INK))
    return fig


def altitude_ranges(summary: pd.DataFrame, *, labels: Mapping[str, str],
                    colours: Mapping[str, str], unit: str = "km") -> go.Figure:
    """Full ranges and middle halves on a linear axis, using precomputed quartiles.

No logarithmic compression or pooled median hides distinct altitude populations.
The UI offers altitude bands and discloses that these ranges describe the chosen band.
"""
    from src.analysis.overview import KM_PER_MILE

    if summary.empty:
        return _empty("No altitude estimates in this range.", height=260)
    factor = 1 if unit == "km" else 1 / KM_PER_MILE
    fig = go.Figure()
    for row in summary.itertuples():
        label = labels.get(str(row.group), str(row.group))
        colour = colours.get(str(row.group), _ACCENT)
        lo, q1, median, q3, hi = [getattr(row, k) * factor
                                 for k in ("min", "q1", "median", "q3", "max")]
        fig.add_trace(go.Scatter(x=[lo, hi], y=[label, label], mode="lines",
                                 line=dict(color=colour, width=2), hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=[q1, q3], y=[label, label], mode="lines",
                                 line=dict(color=colour, width=12), hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            x=[median], y=[label], mode="markers", name=label,
            marker=dict(color=colour, size=13, line=dict(color=INK, width=2)),
            customdata=[[int(row.n), lo, q1, q3, hi, int(row.missing)]],
            hovertemplate=(f"<b>{label}</b><br>Typical altitude: %{{x:,.0f}} {unit}"
                           f"<br>Middle half: %{{customdata[2]:,.0f}}–%{{customdata[3]:,.0f}} {unit}"
                           f"<br>Full range: %{{customdata[1]:,.0f}}–%{{customdata[4]:,.0f}} {unit}"
                           "<br>%{customdata[0]:,} altitude estimates<extra></extra>"),
        ))
    _theme(fig, height=max(260, len(summary) * 55 + 80),
           x_title=f"Height above Earth ({unit})", showlegend=False)
    fig.update_layout(margin=dict(l=10, r=25, t=15, b=10))
    fig.update_xaxes(type="linear", rangemode="tozero", tickformat=",.0f")
    fig.update_yaxes(showgrid=False, tickfont=dict(size=13, color=INK))
    return fig


def align_order(frame: pd.DataFrame, by: str) -> list[str]:
    """Group order used consistently by every chart: largest group first."""
    return [str(name) for name in frame[by].astype(str).value_counts().index]


def _with_alpha(hex_colour: str, alpha: float = 0.35) -> str:
    """Convert ``#rrggbb`` to an rgba string with the given alpha."""
    value = hex_colour.lstrip("#")
    if len(value) != 6:
        return hex_colour
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def regime_scatter(
    frame: pd.DataFrame,
    *,
    x: str = "inclination_deg",
    y: str = "altitude_km",
    colours: Mapping[str, str] | None = None,
    highlight_ids: Iterable[int] = (),
    log_y: bool = True,
    height: int = 460,
) -> go.Figure:
    """Altitude against inclination - the orbital-regime map.

    One trace per (constellation, regime): colour identifies the constellation, marker shape
    the orbital regime, and the legend collapses the regimes under one entry per
    constellation. Every point carries its NORAD id in ``customdata`` so a click can be
    mapped back to a satellite (:func:`selection_norad_ids`).

    Points in ``highlight_ids`` are drawn in a separate, larger trace on top, so a selection
    made elsewhere stays visible without hiding the context.
    """
    if frame.empty:
        return _empty("No observations in the current selection.")
    colours = dict(colours or {})
    highlight = {int(value) for value in highlight_ids}
    scatter = go.Scattergl if len(frame) > GL_THRESHOLD else go.Scatter

    fig = go.Figure()
    seen: set[str] = set()
    for (constellation, regime), group in frame.groupby(
        ["constellation", "regime"], observed=True
    ):
        constellation, regime = str(constellation), str(regime)
        colour = colours.get(constellation, _ACCENT)
        masked = group["norad_id"].isin(highlight)
        plain = group.loc[~masked]
        fig.add_trace(scatter(
            x=plain[x],
            y=plain[y],
            mode="markers",
            name=constellation,
            legendgroup=constellation,
            showlegend=constellation not in seen,
            marker={
                "color": colour, "size": 5, "opacity": 0.55,
                "symbol": REGIME_SYMBOLS.get(regime, "circle"),
                "line": {"width": 0},
            },
            customdata=np.stack([
                plain["norad_id"],
                plain["name"].astype(str),
                plain["regime"].astype(str),
                plain["altitude_km"],
                plain["inclination_deg"],
                plain["period_min"],
            ], axis=-1),
            hovertemplate=(
                "<b>%{customdata[1]}</b> NORAD %{customdata[0]}<br>"
                "%{customdata[2]} &middot; %{x:.2f} deg &middot; "
                "%{y:.0f} km<br>period %{customdata[5]:.0f} min<extra></extra>"
            ),
        ))
        seen.add(constellation)

    if highlight:
        picked = frame.loc[frame["norad_id"].isin(highlight)]
        if not picked.empty:
            fig.add_trace(scatter(
                x=picked[x], y=picked[y], mode="markers",
                name="highlighted",
                marker={
                    "color": "#ffffff", "size": 11, "opacity": 0.95,
                    "symbol": "star", "line": {"width": 1, "color": _ACCENT},
                },
                customdata=np.stack([
                    picked["norad_id"], picked["name"].astype(str),
                ], axis=-1),
                hovertemplate="<b>%{customdata[1]}</b> (NORAD %{customdata[0]})"
                              "<extra></extra>",
            ))

    _theme(fig, height=height, x_title=_axis_title(x), y_title=_axis_title(y))
    if log_y:
        fig.update_yaxes(type="log",
                         title={"text": f"{_axis_title(y)} [log scale]"})
    # No in-figure title: the section heading explains the chart, and a title at the top
    # would collide with the horizontal legend.
    fig.update_layout(hovermode="closest")
    return fig


def distribution_box(
    frame: pd.DataFrame,
    column: str,
    by: str = "constellation",
    *,
    kind: str = "box",
    colours: Mapping[str, str] | None = None,
    height: int = 400,
) -> go.Figure:
    """Distribution of one documented variable across groups.

    ``kind="box"`` shows quartiles and the same Tukey whiskers the outlier analysis uses;
    ``kind="violin"`` adds density, which is what reveals a multi-modal group. Boxes for a
    mixed group are honest but flat - the violin is what shows *why* half of it is flagged.
    """
    if frame.empty or column not in frame.columns:
        return _empty("Nothing to plot for this variable.")
    colours = dict(colours or {})
    fig = go.Figure()
    for group in align_order(frame, by):
        subset = frame.loc[frame[by].astype(str) == group, column].dropna()
        if subset.empty:
            continue
        base = group.split(" / ")[0]
        colour = colours.get(base, _ACCENT)
        if kind == "violin":
            fig.add_trace(go.Violin(
                y=subset, name=group, box_visible=True, meanline_visible=True,
                points="outliers", line={"color": colour},
                fillcolor=_with_alpha(colour), marker={"size": 3},
            ))
        else:
            fig.add_trace(go.Box(
                y=subset, name=group, boxpoints="outliers",
                boxmean="sd", jitter=0.4, pointpos=0,
                line={"color": colour}, marker={"color": colour, "size": 4},
            ))
    _theme(fig, height=height, y_title=_axis_title(column), showlegend=False)
    fig.update_xaxes(tickangle=-20)
    return fig


def outlier_strip(
    frame: pd.DataFrame,
    column: str,
    by: str = "constellation",
    *,
    summary: pd.DataFrame | None = None,
    height: int = 380,
) -> go.Figure:
    """Every observation, with group medians and IQR fences drawn on top.

    Nothing is filtered out: the strip shows all points, the median marks the centre and the
    dotted fences show where the rule fired. A point outside a fence is a prompt to look,
    not an error - and a strip that splits into two clouds is telling you the group is a
    mixture, not that half its objects are broken.
    """
    if frame.empty or column not in frame.columns:
        return _empty("Nothing to plot for this variable.")
    fig = go.Figure()
    order = align_order(frame, by)
    index_of = {name: position for position, name in enumerate(order)}

    rng = np.random.default_rng(7)
    for group in order:
        subset = frame.loc[frame[by].astype(str) == group, column].dropna()
        if subset.empty:
            continue
        jitter = rng.uniform(-0.22, 0.22, len(subset))
        fig.add_trace(go.Scattergl(
            x=jitter + index_of[group], y=subset, mode="markers",
            name=group, showlegend=False,
            marker={"color": _ACCENT, "size": 4, "opacity": 0.45},
            hovertemplate=f"<b>{group}</b><br>%{{y:.4g}}<extra></extra>",
        ))

    if summary is not None and not summary.empty:
        observed = frame[column].dropna()
        if not observed.empty:
            low, high = float(observed.min()), float(observed.max())
            # A fence far outside the data (e.g. -52 000 km for a bimodal group) would
            # stretch the axis and squash every point into a line; such a fence carries no
            # information about where the data is, so it is not drawn.
            margin = 0.15 * max(high - low, 1e-9)
        else:
            low = high = margin = 0.0
        for _, row in summary.iterrows():
            group = str(row["group"])
            median = row.get("median")
            if group not in index_of or median is None or pd.isna(median):
                continue
            position = index_of[group]
            fig.add_trace(go.Scatter(
                x=[position], y=[float(median)], mode="markers", showlegend=False,
                marker={"color": _WARN, "size": 12, "symbol": "line-ew"},
                hovertemplate=f"{group} median {float(median):.4g}<extra></extra>",
            ))
            for fence in ("lower_fence", "upper_fence"):
                value = row.get(fence)
                if value is None or pd.isna(value):
                    continue
                if not (low - margin <= float(value) <= high + margin):
                    continue
                fig.add_shape(
                    type="line", x0=position - 0.35, x1=position + 0.35,
                    y0=float(value), y1=float(value),
                    line={"color": _BAD, "width": 1, "dash": "dot"},
                )

    _theme(fig, height=height, y_title=_axis_title(column), showlegend=False)
    fig.update_xaxes(
        tickmode="array", tickvals=list(index_of.values()), ticktext=list(index_of),
        tickangle=-20, showgrid=False,
    )
    return fig


def freshness_bar(
    table: pd.DataFrame, *, stale_hours: float, height: int = 300
) -> go.Figure:
    """Median element age per group, with the observed min-max range as an error bar.

    The stale threshold is drawn as a dotted line. Age is measured against the scene anchor,
    so the chart answers "how current is this snapshot", not "how old is it now".
    """
    if table.empty:
        return _empty("No element ages available.")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=table["group"].astype(str),
        y=table["median_age_h"],
        error_y={
            "type": "data", "symmetric": False,
            "array": table["max_age_h"] - table["median_age_h"],
            "arrayminus": table["median_age_h"] - table["min_age_h"],
            "color": _MUTED, "thickness": 1, "width": 3,
        },
        marker={"color": _ACCENT, "opacity": 0.75},
        customdata=np.stack([
            table["n"], table["min_age_h"], table["max_age_h"], table["oldest_name"],
        ], axis=-1),
        hovertemplate=(
            "<b>%{x}</b><br>median %{y:.1f} h"
            "<br>range %{customdata[1]:.1f}-%{customdata[2]:.1f} h"
            "<br>n %{customdata[0]}<br>oldest: %{customdata[3]}<extra></extra>"
        ),
    ))
    fig.add_hline(
        y=stale_hours, line={"color": _BAD, "width": 1, "dash": "dot"},
        annotation={
            "text": f"stale threshold {stale_hours:.0f} h",
            "font": {"size": 10, "color": _BAD}, "x": 0, "xanchor": "left",
        },
    )
    _theme(fig, height=height, y_title="element age (h)", showlegend=False)
    fig.update_xaxes(tickangle=-20)
    return fig


def missing_bar(table: pd.DataFrame, *, height: int = 280) -> go.Figure:
    """Share of absent values per column, coloured by provenance class."""
    if table.empty:
        return _empty("No columns to audit.")
    palette = {
        "identity": "#8fb4d8", "grouping": "#6f8fb0", "element": "#58b6ff",
        "derived": "#7ee0b8", "propagated": "#ffb454",
    }
    with_missing = table.loc[table["missing"] > 0]
    shown = with_missing if not with_missing.empty else table
    colours = [palette.get(kind, _MUTED) for kind in shown["provenance"]]
    fig = go.Figure(go.Bar(
        x=shown["column"], y=shown["missing_pct"], marker={"color": colours},
        customdata=np.stack(
            [shown["provenance"], shown["missing"], shown["n"]], axis=-1
        ),
        hovertemplate=(
            "<b>%{x}</b><br>%{y:.2f}% missing (%{customdata[1]} of %{customdata[2]})"
            "<br>provenance: %{customdata[0]}<extra></extra>"
        ),
    ))
    _theme(fig, height=height, y_title="missing (%)", showlegend=False)
    fig.update_xaxes(tickangle=-35)
    if with_missing.empty:
        fig.add_annotation(
            text="No missing values in this snapshot - every documented column is complete.",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.92,
            font={"color": _MUTED, "size": 11},
        )
    return fig


def correlation_heatmap(
    matrix: pd.DataFrame, *, height: int = 420, title: str = ""
) -> go.Figure:
    """Annotated Spearman matrix on a diverging scale centred at zero."""
    if matrix.empty:
        return _empty("Select at least two variables.")
    labels = [str(name) for name in matrix.columns]
    fig = go.Figure(go.Heatmap(
        z=matrix.to_numpy(), x=labels, y=labels,
        zmin=-1, zmax=1, colorscale="RdBu", reversescale=True,
        text=np.round(matrix.to_numpy(), 2), texttemplate="%{text}",
        textfont={"size": 10},
        colorbar={"title": {"text": "rho", "font": {"size": 10}},
                  "tickfont": {"size": 9}, "thickness": 10},
        hovertemplate="%{y} ~ %{x}<br>rho %{z:.3f}<extra></extra>",
    ))
    _theme(fig, height=height, showlegend=False)
    fig.update_xaxes(tickangle=-30, showgrid=False)
    fig.update_yaxes(showgrid=False, autorange="reversed")
    if title:
        fig.update_layout(title={"text": title, "font": {"size": 11, "color": _MUTED},
                                 "x": 0, "xanchor": "left"})
    return fig


def pooled_within_chart(
    comparison: pd.DataFrame,
    *,
    per_group: pd.DataFrame | None = None,
    height: int = 420,
) -> go.Figure:
    """Dumbbell chart: the pooled coefficient against each group's own coefficient.

    The pooled value and the within-group median are joined by a line so the size of the
    difference is visible, and - when ``per_group`` is supplied for a single pair - every
    individual group is drawn, because a median of a few coefficients can hide a genuine
    reversal in one of them.

    The chart deliberately does not annotate "reversal": it shows direction per group and
    the caller decides what the evidence supports.
    """
    if comparison.empty:
        return _empty("Select at least two variables.")
    fig = go.Figure()
    pairs = comparison["pair"].astype(str).tolist()

    if per_group is not None and not per_group.empty and len(comparison) == 1:
        defined = per_group.loc[per_group["defined"]]
        fig.add_trace(go.Scatter(
            x=defined["rho"], y=[pairs[0]] * len(defined), mode="markers+text",
            text=defined["group"], textposition="middle right",
            textfont={"size": 9, "color": _MUTED},
            marker={"color": _ACCENT, "size": 8, "opacity": 0.8},
            name="per group",
            hovertemplate="%{text}: %{x:.3f}<extra></extra>",
        ))

    fig.add_trace(go.Scatter(
        x=comparison["within_median"], y=pairs, mode="markers",
        name="within-group median",
        marker={"color": _WARN, "size": 11, "symbol": "diamond"},
        customdata=np.stack([comparison["within_min"], comparison["within_max"],
                             comparison["n_groups_defined"]], axis=-1),
        hovertemplate=("%{y}<br>within median %{x:.3f}"
                       "<br>range %{customdata[0]:.3f} to %{customdata[1]:.3f}"
                       "<br>groups %{customdata[2]}<extra></extra>"),
    ))
    for _, row in comparison.iterrows():
        if pd.notna(row["within_min"]) and pd.notna(row["within_max"]):
            fig.add_trace(go.Scatter(
                x=[row["within_min"], row["within_max"]],
                y=[row["pair"], row["pair"]], mode="lines",
                line={"color": _MUTED, "width": 2}, showlegend=False, hoverinfo="skip",
            ))
    fig.add_trace(go.Scatter(
        x=comparison["pooled_rho"], y=pairs, mode="markers",
        name="pooled",
        marker={"color": "#ffffff", "size": 10, "symbol": "circle",
                "line": {"width": 1, "color": _ACCENT}},
        hovertemplate="%{y}<br>pooled %{x:.3f}<extra></extra>",
    ))
    fig.add_vline(x=0, line={"color": _MUTED, "width": 1, "dash": "dot"})
    _theme(fig, height=height, x_title="Spearman rho", showlegend=True)
    fig.update_yaxes(showgrid=False)
    return fig


def selection_norad_ids(figure: go.Figure, state: object) -> list[int]:
    """Map a Plotly selection event back to NORAD ids, without trusting the payload shape.

    Streamlit returns selected points with a curve index and a point index; the NORAD id is
    the first element of that point's ``customdata``. The lookup is done here against the
    figure itself, so the result does not depend on Streamlit echoing ``customdata`` back.

    Returns a de-duplicated, sorted list. Anything unexpected yields ``[]``: a selection that
    cannot be mapped must not highlight the wrong satellite.
    """
    try:
        points = state["selection"]["points"]  # type: ignore[index]
    except (TypeError, KeyError, IndexError):
        return []
    found: set[int] = set()
    for point in points or []:
        try:
            curve = int(point.get("curve_number", point.get("curve", -1)))
            index = int(point.get("point_index", point.get("point_number", -1)))
        except (AttributeError, TypeError, ValueError):
            continue
        if curve < 0 or index < 0 or curve >= len(figure.data):
            continue
        customdata = figure.data[curve].customdata
        if customdata is None or index >= len(customdata):
            continue
        try:
            found.add(int(customdata[index][0]))
        except (TypeError, ValueError, IndexError):
            continue
    return sorted(found)
