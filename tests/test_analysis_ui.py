"""Exercise dashboard widgets against real offline data through Streamlit reruns."""
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def app():
    test = AppTest.from_string('''
import app
from src.ui.loading import load_constellation_cached
app.load_constellation_cached = lambda groups, window, offline, refresh, when: load_constellation_cached(groups, window, True, False, when)
app.main()
''', default_timeout=30).run()
    assert not test.exception
    assert test.checkbox(key="group_starlink").value
    assert len(test.dataframe) > 5
    return test


def test_filter_units_empty_selection_and_highlight_lifecycle(app):
    app.checkbox(key="group_starlink").uncheck().run()
    app.multiselect(key="analysis_groups").set_value(["gps-ops"]).run()
    assert not app.exception
    # The single-group filter reaches the overview, statistics and original orbit map.
    tables = [element.value for element in app.dataframe]
    descriptive = next(t for t in tables if {"variable", "mean", "sd"} <= set(t.columns))
    assert set(descriptive["group"]) == {"gps-ops"}
    app.button(key="analysis_highlight_group_button").click().run()
    ids = app.session_state["orbix_highlight_ids"]
    assert len(ids) == 32
    app.radio(key="analysis_units").set_value("mi").run()
    assert not app.exception
    assert not app.checkbox(key="group_starlink").value
    assert app.session_state["orbix_highlight_ids"] == ids
    app.multiselect(key="analysis_groups").set_value([]).run()
    assert not app.exception
    assert app.session_state["orbix_highlight_ids"] == []
    assert any("Choose at least one group" in item.value for item in app.info)
    app.multiselect(key="analysis_groups").set_value(["weather"]).run()
    assert not app.exception
    assert len(app.dataframe) > 5


def test_altitude_band_and_diagnostics_controls_are_usable(app):
    original_overview = [m.value for m in app.markdown if 'ox-analysis-metrics' in m.value]
    app.radio(key="analysis_altitude_band").set_value("Higher Orbits").run()
    assert not app.exception
    assert [m.value for m in app.markdown if 'ox-analysis-metrics' in m.value] == original_overview
    app.multiselect(key="analysis_corr_vars").set_value([]).run()
    assert not app.exception
    assert any("Select at least two" in item.value for item in app.caption)
    app.radio(key="analysis_out_by").set_value("stratum").run()
    app.slider(key="analysis_out_iqr").set_value(2.0).run()
    assert not app.exception
    app.checkbox(key="group_weather").uncheck().run()
    assert not app.exception
    assert "weather" not in app.multiselect(key="analysis_groups").value


def test_discovery_and_unusual_satellites_share_globe_selection(app):
    app.button(key="analysis_farthest_view").click().run()
    assert not app.exception
    selected = app.session_state["orbix_selected"]
    frame = app.session_state["orbix_analysis_frame"]["frame"]
    assert selected == int(frame.loc[frame["altitude_km"].idxmax(), "norad_id"])
    assert app.session_state["orbix_highlight_ids"] == [selected]
    action = app.session_state["analysis_globe_action"]
    assert action["kind"] == "select" and action["ids"] == [selected]
    app.radio(key="analysis_units").set_value("mi").run()
    assert app.session_state["analysis_globe_action"] == action
    app.button(key="analysis_farthest_view").click().run()
    assert app.session_state["analysis_globe_action"]["revision"] > action["revision"]
    app.button(key="analysis_investigate_button").click().run()
    assert not app.exception
    candidates = app.selectbox(key="analysis_unusual_satellite").options
    assert 0 < len(candidates) <= 50
    choice = app.selectbox(key="analysis_unusual_satellite").value
    app.text_input(key="analysis_unusual_search").set_value(str(choice)).run()
    assert len(app.selectbox(key="analysis_unusual_satellite").options) == 1
    app.button(key="analysis_unusual_view").click().run()
    assert not app.exception
    assert app.session_state["orbix_selected"] == choice
    app.text_input(key="analysis_unusual_search").set_value("no such satellite").run()
    assert any("No unusual satellites match" in item.value for item in app.info)


def test_missing_position_cannot_be_selected(app, monkeypatch):
    from src.ui import analysis_cache
    frame = app.session_state["orbix_analysis_frame"]["frame"].copy()
    missing_id = int(frame.iloc[0]["norad_id"])
    frame.loc[frame["norad_id"] == missing_id, "altitude_km"] = float("nan")
    monkeypatch.setattr(analysis_cache, "analysis_frame", lambda scene: frame)
    app.selectbox(key="analysis_satellite").set_value(missing_id).run()
    assert not app.exception
    assert app.button(key="analysis_satellite_view").disabled
    assert any("No position estimate" in item.value for item in app.info)


def test_interactions_keep_snapshot_after_shared_cache_expiry(app):
    from src.ui.loading import load_constellation_cached
    scene = app.session_state["orbix_loaded_scene"]["scene"]
    anchor = scene.plan.start
    load_constellation_cached.clear()  # Models an expired/evicted shared cache.
    app.radio(key="analysis_units").set_value("mi").run()
    app.multiselect(key="analysis_groups").set_value(["weather"]).run()
    assert not app.exception
    assert app.session_state["orbix_loaded_scene"]["scene"].plan.start == anchor
    assert app.session_state["orbix_analysis_frame"]["frame"].attrs["anchor"] == anchor


def test_large_unusual_results_are_paged_and_group_search_is_scoped(app):
    app.checkbox(key="group_starlink").check().run()
    assert not app.exception
    assert len(app.selectbox(key="analysis_unusual_satellite").options) == 50
    first = app.selectbox(key="analysis_unusual_satellite").options
    app.number_input(key="analysis_unusual_page").set_value(2).run()
    assert not app.exception
    second = app.selectbox(key="analysis_unusual_satellite").options
    assert len(second) == 50 and set(first).isdisjoint(second)
    app.selectbox(key="analysis_unusual_group").set_value("oneweb").run()
    assert app.number_input(key="analysis_unusual_page").value == 1
    assert all("ONEWEB" in name for name in app.selectbox(key="analysis_unusual_satellite").options)


def test_technical_reference_is_separate_from_exploration(app):
    assert [tab.label for tab in app.tabs] == ["Globe", "Analysis", "User guide"]
    analysis_expanders = {e.label for e in app.tabs[1].expander}
    assert analysis_expanders == {"Explore unusual satellites", "Explore individual satellites"}
    guide_expanders = {e.label for e in app.tabs[2].expander}
    assert "Correlation analysis" in guide_expanders
    assert "AI answer evidence & request details" in guide_expanders
    sidebar_text = " ".join(e.value for e in app.sidebar.markdown) + " ".join(e.value for e in app.sidebar.caption)
    assert "Notes" not in sidebar_text and "One CelesTrak request" not in sidebar_text
    assert "SGP4" not in sidebar_text
    assert not any("SGP4" in caption.value for caption in app.tabs[1].caption)
