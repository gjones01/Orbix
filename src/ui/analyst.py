"""Ask Orbix UI. Analysis controls and globe state remain owned by their existing UI."""
from __future__ import annotations

import uuid

import streamlit as st

from src.analyst import context as grounding, conversation, provider

build_context = st.cache_data(show_spinner=False, max_entries=24)(grounding.build_context)
snapshot_id = st.cache_data(show_spinner=False, max_entries=8)(grounding.snapshot_id)


def configuration() -> provider.Config:
    try:
        secrets = st.secrets.to_dict()
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        secrets = {}
    return provider.load_config(secrets)


def _load_group(group: str) -> None:
    """Runs as a button callback before the sidebar renders on the next run."""
    st.session_state[f"group_{group}"] = True


def render_analyst(frame, groups: list[str], scene=None) -> None:
    st.subheader("Ask Orbix")
    st.write("Explore your satellite data with AI. Ask questions, investigate patterns, "
             "and understand what's happening above Earth.")
    state = st.session_state.setdefault("orbix_analyst", {})
    if conversation.sync(state, snapshot_id(frame)):
        st.session_state["orbix_analyst_reset_notice"] = True
    if st.session_state.get("orbix_analyst_reset_notice"):
        st.info("The loaded snapshot changed. Your previous conversation was cleared so answers use the new data.")
    group_text = ", ".join(grounding.LABELS.get(g, g) for g in groups) or "No groups selected"
    st.caption(f"Current snapshot: {frame.attrs.get('anchor')} · {group_text} · "
               f"ID {state['snapshot'][:10]}. Answers use km and degrees; globe playback does not change this snapshot.")
    config = configuration()
    enabled = True
    try:
        config.validate()
    except provider.AnalystError as exc:
        st.info(str(exc))
        enabled = False
    st.caption("Submitting sends your question, relevant computed statistics and up to two recent "
               "turns to the configured AI provider. AI explanations can make mistakes; inspect the evidence below each answer.")
    with st.expander("AI configuration & data use"):
        st.write("Set ORBIX_LLM_BASE_URL, ORBIX_LLM_MODEL and ORBIX_LLM_API_KEY in the server "
                 "environment or .streamlit/secrets.toml. See docs/ai_analyst.md. "
                 "No provider is chosen automatically. Offline snapshot mode disables CelesTrak "
                 "downloads; submitting to a remote AI provider still requires a network connection.")
        if enabled:
            st.text(f"Configured model: {config.model}")
    if st.button("Clear conversation", key="analyst_clear", disabled=not state["turns"] and "error" not in state):
        state.update(turns=[], last_submission=None)
        state.pop("error", None)
        state.pop("missing_groups", None)
        state.pop("data_notice", None)
        st.session_state.pop("orbix_analyst_reset_notice", None)
    question = None
    choices = grounding.suggestions(groups)
    cols = st.columns(len(choices))
    for i, suggestion in enumerate(choices):
        if cols[i].button(suggestion, key=f"analyst_suggest_{i}", disabled=not enabled or not groups):
            question = suggestion
    if st.button("Find interesting patterns", key="analyst_patterns", disabled=not enabled or not groups):
        question = "Find interesting patterns in the selected data. Give 3–5 evidence-based findings with numerical evidence and caveats."
    with st.form("analyst_question_form", clear_on_submit=False):
        entered = st.text_input("Your question", key="analyst_question", max_chars=grounding.MAX_QUESTION,
                                placeholder="Compare groups, ask about an outlier, or enter a NORAD ID…",
                                disabled=not enabled)
        submitted = st.form_submit_button("Ask Orbix", disabled=not enabled or frame.empty)
    if submitted:
        question = entered
    if question is not None:
        state.pop("missing_groups", None)
        state.pop("data_notice", None)
        with st.spinner("Examining this snapshot and preparing an explanation…"):
            try:
                same_scope = [t for t in state["turns"] if t["context"]["analysis_filter"] == groups]
                previous = same_scope[-1]["question"] if same_scope else ""
                evidence = build_context(frame, tuple(groups), question,
                                         st.session_state.get("orbix_selected"), previous)
                if scene is not None:
                    evidence = grounding.attach_raw_records(evidence, scene)
                if evidence["unavailable_groups"]:
                    state["missing_groups"] = evidence["unavailable_groups"]
                    names = ", ".join(grounding.LABELS.get(g, g) for g in evidence["unavailable_groups"])
                    state["data_notice"] = (f"{names} data is not loaded in this snapshot. "
                                      "Load the missing group below or enable it under Constellations to load "
                                      "in the sidebar for its numerical results. The analyst can still explain "
                                      "the concepts and use the available data.")
                elif not evidence["total_satellites"]:
                    missing_ids = evidence["missing_norad_ids"]
                    state["data_notice"] = ("NORAD " + ", ".join(map(str, missing_ids)) + " is not in the loaded snapshot. "
                                      "The analyst can explain the requested fields but cannot supply this object's missing values."
                                      if missing_ids else
                                      "No records are available for this scope. The analyst can explain concepts but cannot invent its values.")
                conversation.submit(state, uuid.uuid4().hex, config, evidence, question)
                st.session_state.pop("orbix_analyst_reset_notice", None)
            except ValueError as exc:
                state["error"] = str(exc)
    if state.get("error"):
        st.warning(state["error"])
    if state.get("data_notice"):
        st.info(state["data_notice"])
    for group in state.get("missing_groups", []):
        if st.session_state.get(f"group_{group}"):
            st.caption(f"{grounding.LABELS.get(group, group)} is enabled but has no records. "
                       "Check its data-loading report on the Globe tab, then reload the scene.")
        else:
            st.button(f"Load {grounding.LABELS.get(group, group)}", key=f"analyst_load_{group}",
                      on_click=_load_group, args=(group,))
    for turn in state["turns"]:
        with st.chat_message("user"):
            st.text(turn["question"])
        with st.chat_message("assistant"):
            ctx = turn["context"]
            st.caption(f"Based on Orbix snapshot {ctx['snapshot'][:10]} · {ctx['position_timestamp_utc']} · "
                       + ", ".join(grounding.LABELS.get(g, g) for g in ctx["answer_groups"]))
            if ctx["analysis_filter"] != groups:
                st.caption("Earlier answer — the Analysis group selection has since changed.")
            for finding in turn["answer"]["findings"]:
                # Plain text prevents provider/user content from injecting links or images.
                st.text(finding["title"])
                st.text(finding["explanation"])
                if finding["caveat"].strip():
                    st.text("Note: " + finding["caveat"])
            with st.expander("Computed evidence & request details"):
                st.json(ctx)
                if turn["answer"].get("usage"):
                    st.json(turn["answer"]["usage"])
