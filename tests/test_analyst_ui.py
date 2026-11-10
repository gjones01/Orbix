"""Run the real Ask Orbix UI and provider boundary with mocked HTTP only."""
import json
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from src.analyst import provider
from test_analyst import response


@pytest.fixture
def app(monkeypatch):
    from src.ui import analyst
    # Developer-local credentials must never affect these hermetic UI tests.
    monkeypatch.setattr(analyst, "configuration", lambda: provider.load_config({}))
    monkeypatch.setenv("ORBIX_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("ORBIX_LLM_MODEL", "test-model")
    monkeypatch.setenv("ORBIX_LLM_API_KEY", "test-key")
    return AppTest.from_string('''
import app
from src.ui.loading import load_constellation_cached
app.load_constellation_cached = lambda g,w,o,r,t: load_constellation_cached(g,w,True,False,t)
app.main()
''', default_timeout=30).run()


def test_explicit_requests_only_filter_history_clear_and_scene_reset(app, monkeypatch):
    post = Mock(return_value=response())
    monkeypatch.setattr(provider.requests, "post", post)
    assert not app.exception
    assert post.call_count == 0
    app.button(key="analyst_suggest_0").click().run()
    assert not app.exception and post.call_count == 1
    assert len(app.session_state["orbix_analyst"]["turns"]) == 1
    app.radio(key="analysis_units").set_value("mi").run()
    assert post.call_count == 1
    app.multiselect(key="analysis_groups").set_value(["gps-ops"]).run()
    assert post.call_count == 1
    assert any("Earlier answer" in c.value for c in app.caption)
    app.button(key="analyst_patterns").click().run()
    assert not app.exception and post.call_count == 2
    ctx = json.loads(post.call_args.kwargs["json"]["messages"][-1]["content"])["current_snapshot_evidence"]
    assert ctx["answer_groups"] == ["gps-ops"] and ctx["total_satellites"] == 32
    assert len(post.call_args.kwargs["json"]["messages"]) == 2
    app.button(key="analyst_clear").click().run()
    assert not app.session_state["orbix_analyst"]["turns"] and post.call_count == 2
    app.button(key="analyst_suggest_0").click().run()
    app.checkbox(key="group_oneweb").uncheck().run()
    assert not app.exception and post.call_count == 3
    assert not app.session_state["orbix_analyst"]["turns"]
    assert any("snapshot changed" in i.value for i in app.info)


def test_missing_configuration_and_error_recovery(app, monkeypatch):
    post = Mock(return_value=response(status=429))
    monkeypatch.setattr(provider.requests, "post", post)
    app.button(key="analyst_patterns").click().run()
    assert not app.exception
    assert any("quota" in i.value for i in app.warning)
    app.run()
    assert post.call_count == 1
    monkeypatch.delenv("ORBIX_LLM_API_KEY")
    app.run()
    assert not app.exception and app.button(key="analyst_patterns").disabled
    assert any("not configured" in i.value for i in app.info)


def test_starlink_question_load_and_resubmit(app, monkeypatch):
    post = Mock(return_value=response())
    monkeypatch.setattr(provider.requests, "post", post)
    question = "Why are the starlink satellites so much lower altitude than the others?"
    app.text_input(key="analyst_question").set_value(question)
    app.button(key="FormSubmitter:analyst_question_form-Ask Orbix").click().run()
    assert not app.exception and post.call_count == 1
    assert any("Starlink data is not loaded" in notice.value for notice in app.info)
    assert app.text_input(key="analyst_question").value == question
    app.button(key="analyst_load_starlink").click().run()
    assert not app.exception
    assert app.checkbox(key="group_starlink").value
    assert post.call_count == 1  # Loading a group does not submit another request.
    assert app.text_input(key="analyst_question").value == question
    app.button(key="FormSubmitter:analyst_question_form-Ask Orbix").click().run()
    assert not app.exception and post.call_count == 2
    ctx = json.loads(post.call_args.kwargs["json"]["messages"][-1]["content"])["current_snapshot_evidence"]
    assert "starlink" in ctx["answer_groups"] and "oneweb" in ctx["answer_groups"]
    assert ctx["total_satellites"] > 10000
    assert len(app.session_state["orbix_analyst"]["turns"]) == 1
    app.run()
    assert post.call_count == 2


def test_partial_comparison_still_reaches_model_with_missing_group_notice(app, monkeypatch):
    post = Mock(return_value=response())
    monkeypatch.setattr(provider.requests, "post", post)
    app.text_input(key="analyst_question").set_value("Compare Starlink and OneWeb")
    app.button(key="FormSubmitter:analyst_question_form-Ask Orbix").click().run()
    assert not app.exception and post.call_count == 1
    assert app.button(key="analyst_load_starlink")
    ctx = json.loads(post.call_args.kwargs["json"]["messages"][-1]["content"])["current_snapshot_evidence"]
    assert ctx["unavailable_groups"] == ["starlink"]
    assert {row["group"] for row in ctx["evidence"]["population"]} == {"oneweb"}


def test_raw_api_fields_reach_model_outside_analysis_filter(app, monkeypatch):
    post = Mock(return_value=response())
    monkeypatch.setattr(provider.requests, "post", post)
    app.multiselect(key="analysis_groups").set_value(["gps-ops"]).run()
    app.text_input(key="analyst_question").set_value("Explain BSTAR and MEAN_MOTION_DDOT from the API for NORAD 25544")
    app.button(key="FormSubmitter:analyst_question_form-Ask Orbix").click().run()
    assert not app.exception and post.call_count == 1
    ctx = json.loads(post.call_args.kwargs["json"]["messages"][-1]["content"])["current_snapshot_evidence"]
    assert ctx["answer_groups"] == ["stations"]
    raw = ctx["evidence"]["raw_api_records"]["records"][0]
    scene = app.session_state["orbix_loaded_scene"]["scene"]
    assert raw["omm"] == dict(scene.element(25544).raw)
    assert ctx["evidence"]["satellites"][0]["norad_id"] == raw["norad_id"] == 25544
