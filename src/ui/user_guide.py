"""User help and technical reference, separate from the exploration workspace."""
from pathlib import Path

import streamlit as st

from src.ui.analysis_tab import render_analysis_reference
from src.ui.panels import render_scene_report


def render_user_guide(scene, payload_bytes: int) -> None:
    st.title("User guide")
    st.markdown((Path(__file__).resolve().parents[2] / "docs" / "user_guide.md").read_text(encoding="utf-8"))
    with st.expander("Loaded data & simulation details"):
        st.checkbox("Force re-download", key="guide_force_refresh",
                    help="Bypass the two-hour catalogue cache. CelesTrak may return unchanged data.")
        render_scene_report(scene, payload_bytes)
    with st.expander("Ask Orbix: configuration & data use"):
        st.markdown("Configure `ORBIX_LLM_BASE_URL`, `ORBIX_LLM_MODEL` and `ORBIX_LLM_API_KEY` "
                    "in the server environment or `.streamlit/secrets.toml`. "
                    "The full setup and limitations are documented in `docs/ai_analyst.md`. "
                    "Edit `prompts/analyst_style.md` to adjust response style.")
    with st.expander("AI answer evidence & request details"):
        turns = st.session_state.get("orbix_analyst", {}).get("turns", [])
        if not turns:
            st.caption("Ask a question in Analysis to inspect its supporting evidence here.")
        for turn in turns:
            st.text(turn["question"])
            st.json(turn["context"])
            if turn["answer"].get("usage"):
                st.json(turn["answer"]["usage"])
    render_analysis_reference(scene)
