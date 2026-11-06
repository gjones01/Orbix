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


def render_analyst(frame, groups: list[str]) -> None:
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
        st.session_state.pop("orbix_analyst_reset_notice", None)
    question = None
    choices = grounding.suggestions(groups)
    cols = st.columns(len(choices))
    for i, suggestion in enumerate(choices):
        if cols[i].button(suggestion, key=f"analyst_suggest_{i}", disabled=not enabled or not groups):
            question = suggestion
    if st.button("Find interesting patterns", key="analyst_patterns", disabled=not enabled or not groups):
        question = "Find interesting patterns in the selected data. Give 3–5 evidence-based findings with numerical evidence and caveats."
    with st.form("analyst_question_form", clear_on_submit=True):
        entered = st.text_input("Your question", key="analyst_question", max_chars=grounding.MAX_QUESTION,
                                placeholder="Compare groups, ask about an outlier, or enter a NORAD ID…",
                                disabled=not enabled)
        submitted = st.form_submit_button("Ask Orbix", disabled=not enabled or frame.empty)
    if submitted:
        question = entered
    if question is not None:
        with st.spinner("Examining this snapshot and preparing an explanation…"):
            try:
                same_scope = [t for t in state["turns"] if t["context"]["analysis_filter"] == groups]
                previous = same_scope[-1]["question"] if same_scope else ""
                evidence = build_context(frame, tuple(groups), question,
                                         st.session_state.get("orbix_selected"), previous)
                if not evidence["total_satellites"]:
                    state["error"] = "No records match this question. Select or load a group, or use a NORAD ID present in the snapshot."
                else:
                    conversation.submit(state, uuid.uuid4().hex, config, evidence, question)
                st.session_state.pop("orbix_analyst_reset_notice", None)
            except ValueError as exc:
                state["error"] = str(exc)
    if state.get("error"):
        st.warning(state["error"])
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
                st.text("Caveat: " + finding["caveat"])
            with st.expander("Computed evidence & request details"):
                st.json(ctx)
                if turn["answer"].get("usage"):
                    st.json(turn["answer"]["usage"])
