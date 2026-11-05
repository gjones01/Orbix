"""Dark aerospace theme for the Orbix Streamlit shell.

Two responsibilities: page configuration, and one CSS block that styles Streamlit's
own chrome to match the 3D globe. Everything is scoped to stable ``data-testid``
selectors plus Orbix's own ``ox-`` classes, so it degrades gracefully if Streamlit
changes its internals.
"""

from __future__ import annotations

import streamlit as st

__all__ = ["ACCENT", "configure_page", "inject_css"]

ACCENT = "#58b6ff"

_CSS = """
<style>
  :root {
    --ox-bg: #04060b;
    --ox-panel: rgba(11, 17, 27, 0.92);
    --ox-panel-border: rgba(88, 182, 255, 0.16);
    --ox-ink: #dbe6f2;
    --ox-ink-muted: #7c8da3;
    --ox-accent: #58b6ff;
    --ox-mono: "JetBrains Mono", "Cascadia Mono", Consolas, ui-monospace, monospace;
  }

  .stApp {
    background: radial-gradient(ellipse 90% 60% at 50% 0%, #08131f 0%, #04060b 60%);
  }

  [data-testid="stHeader"] { background: transparent; }
  #MainMenu, footer { visibility: hidden; }

  /* Hide Streamlit's deploy button, status widget and decoration bar so the page
     reads as an application rather than a dashboard. */
  [data-testid="stToolbar"],
  [data-testid="stDecoration"],
  [data-testid="stStatusWidget"],
  .stAppDeployButton,
  [data-testid="stAppDeployButton"] {
    display: none !important;
    visibility: hidden !important;
  }

  /* The app owns its layout: hide the sidebar toggle too, which otherwise floats
     as an unlabelled bubble over the globe header. */
  [data-testid="stSidebarCollapseButton"],
  [data-testid="stSidebarCollapsedControl"] {
    display: none !important;
  }

  .block-container {
    padding-top: 1.1rem;
    padding-bottom: 0.6rem;
    max-width: 1800px;
  }

  /* ---- sidebar -------------------------------------------------------- */
  [data-testid="stSidebar"] {
    background: #060a11;
    border-right: 1px solid rgba(88, 182, 255, 0.10);
  }
  [data-testid="stSidebar"] h2,
  [data-testid="stSidebar"] h3 {
    font-size: 0.72rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--ox-ink-muted);
    font-weight: 600;
    margin: 1.15rem 0 0.45rem 0;
  }
  [data-testid="stSidebar"] p,
  [data-testid="stSidebar"] li,
  [data-testid="stSidebar"] label {
    font-size: 0.82rem;
    color: #a9b8c9;
  }

  /* ---- header --------------------------------------------------------- */
  .ox-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 0 2px 12px 2px;
    border-bottom: 1px solid rgba(88, 182, 255, 0.10);
    margin-bottom: 14px;
  }
  .ox-brand { display: flex; align-items: baseline; gap: 10px; }
  .ox-wordmark {
    font-size: 1.32rem;
    font-weight: 700;
    letter-spacing: 0.34em;
    color: #f2f7ff;
  }
  .ox-brand .sep { color: var(--ox-accent); opacity: 0.75; }
  .ox-tagline {
    font-size: 0.72rem;
    letter-spacing: 0.20em;
    text-transform: uppercase;
    color: var(--ox-ink-muted);
  }

  /* ---- status chip ---------------------------------------------------- */
  .ox-chip {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 5px 11px;
    border-radius: 999px;
    font-family: var(--ox-mono);
    font-size: 0.66rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    border: 1px solid rgba(124, 141, 163, 0.35);
    color: #9fb0c4;
    background: rgba(124, 141, 163, 0.08);
    white-space: nowrap;
  }
  .ox-chip::before {
    content: "";
    width: 6px; height: 6px;
    border-radius: 50%;
    background: currentColor;
  }
  .ox-chip.live {
    color: #6ee7b7;
    border-color: rgba(110, 231, 183, 0.38);
    background: rgba(110, 231, 183, 0.08);
  }
  .ox-chip.cache {
    color: #7fc4ff;
    border-color: rgba(88, 182, 255, 0.38);
    background: rgba(88, 182, 255, 0.08);
  }
  .ox-chip.snapshot {
    color: #ffcc66;
    border-color: rgba(255, 204, 102, 0.38);
    background: rgba(255, 204, 102, 0.08);
  }

  /* ---- information panel ---------------------------------------------- */
  .ox-card {
    background: var(--ox-panel);
    border: 1px solid var(--ox-panel-border);
    border-radius: 10px;
    padding: 0;
    overflow: hidden;
  }
  .ox-card-head {
    padding: 12px 14px 10px 14px;
    border-bottom: 1px solid rgba(88, 182, 255, 0.10);
    background: linear-gradient(180deg, rgba(88, 182, 255, 0.07), rgba(88, 182, 255, 0));
  }
  .ox-card-head .kicker {
    font-size: 0.62rem;
    letter-spacing: 0.20em;
    text-transform: uppercase;
    color: var(--ox-ink-muted);
    display: block;
    margin-bottom: 3px;
  }
  .ox-card-head .name {
    font-size: 0.98rem;
    font-weight: 600;
    color: #f2f7ff;
    letter-spacing: 0.02em;
  }
  .ox-rows { padding: 4px 14px 10px 14px; }
  .ox-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid rgba(124, 141, 163, 0.12);
  }
  .ox-row:last-child { border-bottom: none; }
  .ox-k {
    font-size: 0.66rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ox-ink-muted);
    white-space: nowrap;
  }
  .ox-v {
    font-family: var(--ox-mono);
    font-size: 0.76rem;
    color: #e8f1fb;
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .ox-hint {
    display: block;
    font-size: 0.60rem;
    color: #5f7186;
    letter-spacing: 0.05em;
    text-align: right;
    margin-top: 2px;
    white-space: nowrap;
  }
  .ox-card-foot {
    padding: 9px 14px 11px 14px;
    border-top: 1px solid rgba(88, 182, 255, 0.10);
    font-size: 0.66rem;
    line-height: 1.5;
    color: #6b7d92;
    letter-spacing: 0.02em;
  }

  /* ---- notices -------------------------------------------------------- */
  .ox-notice {
    border-radius: 10px;
    padding: 14px 16px;
    font-size: 0.84rem;
    line-height: 1.6;
    border: 1px solid rgba(255, 120, 120, 0.35);
    background: rgba(255, 90, 90, 0.07);
    color: #ffc0c0;
  }
  .ox-notice strong { color: #ffdada; }
  .ox-notice code {
    font-family: var(--ox-mono);
    font-size: 0.76rem;
    color: #ffe6e6;
    background: rgba(0, 0, 0, 0.25);
    padding: 1px 5px;
    border-radius: 4px;
  }

  .ox-caption {
    font-family: var(--ox-mono);
    font-size: 0.68rem;
    letter-spacing: 0.05em;
    color: #5f7186;
    padding-top: 6px;
  }

  /* Analysis-specific cards: responsive without changing the Globe tab. */
  .ox-analysis-eyebrow {
    color: #8dcaff; font-size: .72rem; letter-spacing: .16em;
    margin-top: 1.2rem; font-weight: 600;
  }
  .ox-analysis-metrics {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 14px; margin: 10px 0 6px;
  }
  .ox-analysis-metric {
    background: #0b111b; border: 1px solid #243244;
    border-radius: 12px; padding: 20px; min-width: 0;
  }
  .ox-analysis-label { color: #a9b8c9; font-size: .81rem; }
  .ox-analysis-value {
    font-size: clamp(1.35rem, 2vw, 2.1rem); font-weight: 650;
    color: #f2f7ff; letter-spacing: -.03em; margin: 9px 0;
    font-variant-numeric: tabular-nums; line-height: 1.25;
  }
  .ox-analysis-detail { color: #a9b8c9; font-size: .75rem; line-height: 1.55; }
  .ox-analysis-discovery {
    border-left: 3px solid #58b6ff; background: #0b111b;
    border-radius: 0 9px 9px 0; padding: 17px 22px; margin-bottom: 6px;
  }
  .ox-analysis-discovery strong { color: #e8f1fb; font-size: .96rem; }
  .ox-analysis-discovery p { color: #b4c2d3; margin: 7px 0 0; line-height: 1.65; font-size: .9rem; }
  @media (max-width: 1200px) {
    .ox-analysis-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 600px) {
    .ox-analysis-metrics { grid-template-columns: 1fr; }
  }

  /* Tighter, more instrument-like buttons in the sidebar. */
  [data-testid="stSidebar"] .stButton button {
    width: 100%;
    background: rgba(88, 182, 255, 0.10);
    border: 1px solid rgba(88, 182, 255, 0.30);
    color: #dceaf8;
    font-size: 0.78rem;
    letter-spacing: 0.06em;
  }
  [data-testid="stSidebar"] .stButton button:hover {
    background: rgba(88, 182, 255, 0.18);
    border-color: rgba(88, 182, 255, 0.55);
    color: #ffffff;
  }
</style>
"""


def configure_page() -> None:
    """Apply Streamlit page configuration. Must be the first Streamlit call."""
    st.set_page_config(
        page_title="Orbix - Orbital View",
        page_icon="\U0001f6f0",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def inject_css() -> None:
    """Inject the Orbix stylesheet once per rerun."""
    st.markdown(_CSS, unsafe_allow_html=True)
