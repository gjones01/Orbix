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
    assert len(test.dataframe) > 5
    return test


def test_filter_units_empty_selection_and_highlight_lifecycle(app):
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
    assert app.session_state["orbix_highlight_ids"] == ids
    app.multiselect(key="analysis_groups").set_value([]).run()
    assert not app.exception
    assert app.session_state["orbix_highlight_ids"] == []
    assert any("Choose at least one group" in item.value for item in app.info)
    app.multiselect(key="analysis_groups").set_value(["weather"]).run()
    assert not app.exception
    assert len(app.dataframe) > 5


def test_altitude_band_and_diagnostics_controls_are_usable(app):
    app.radio(key="analysis_altitude_band").set_value("30,000 km and above").run()
    assert not app.exception
    app.multiselect(key="analysis_corr_vars").set_value([]).run()
    assert not app.exception
    assert any("Select at least two" in item.value for item in app.caption)
    app.radio(key="analysis_out_by").set_value("stratum").run()
    app.slider(key="analysis_out_iqr").set_value(2.0).run()
    assert not app.exception
    app.checkbox(key="group_weather").uncheck().run()
    assert not app.exception
    assert "weather" not in app.multiselect(key="analysis_groups").value
