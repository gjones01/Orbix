# Assignment requirements → Orbix mapping

**Assignment:** *Final Year-End Project — Masters of Data Science, Programming for
Data Science Course*
**Source PDF:** `Final Year-End Project Programming for Data Science Oct 2026.pdf`
(3 pages, Google Docs export, read in full with `pypdf` 6.19.0 on 2026-11-04)
**Project:** Orbix — interactive 3D satellite visualization and orbital intelligence
**Team:** solo final project (one student)
**Document status:** Phase 1 baseline. Updated at the end of each phase.

---

## 1. What the assignment actually requires

The PDF describes a capstone in which students "design and implement a Streamlit
web application that processes and analyzes data from an external API, performs
exploratory data analysis (EDA), and integrates Large Language Model (LLM)
capabilities through an API call." It sets five core requirements, four
deliverables, and a five-part marking scheme (20% each).

### 1.1 Core requirements (from page 1–2)

| # | Requirement group | Specific requirements stated in the PDF |
|---|---|---|
| 1 | **Data Acquisition** | Connect to at least one external API to fetch *real* data; clearly document the source and structure of the data (e.g. JSON, CSV, XML); implement data retrieval and storage logic inside the Streamlit app or a separate module. |
| 2 | **Exploratory Data Analysis** | Descriptive statistics and visualisations; identification and treatment of outliers or missing data; correlation analysis between key variables; insights and summaries displayed within the Streamlit interface. |
| 3 | **LLM Integration** | Demonstrate interaction with an LLM API (open- or closed-source: OpenAI / Anthropic / Gemini, a local tiny LLM, or Claude Code / Codex with an explicit statement that no API is used); the LLM must be used *meaningfully* to enhance insight or automate part of the analysis (summarising findings, generating natural-language reports, interpreting features, answering user queries). |
| 4 | **Streamlit Application** | Clean, organised interface; allows users to interact with the data (filter, visualise, query); displays EDA results; integrates the LLM functionality directly in the app (chatbot, data summary, text generation); includes appropriate error handling, documentation and user guidance. |
| 5 | **Code Quality and Collaboration** | Modular, well-documented, readable code; version control (Git/GitHub) is mandatory; a `README.md` describing project purpose and overview, setup and installation, API and data-source details, and how to run the Streamlit app. |

### 1.2 Deliverables (from page 3)

1. **GitHub repository** — all source code, data files (if applicable) and
   documentation, with a meaningful commit history.
2. **Streamlit app** — hosted locally or on Streamlit Cloud, demonstrating all
   required functionalities.
3. **Project report (2–3 pages)** — objectives, methodology, results, screenshots
   of the Streamlit app, and a reflection on collaboration and lessons learned.
4. **Class live demo and presentation** — in front of the class, maximum 15 minutes.

### 1.3 Evaluation criteria (from page 3)

| Component | Weight |
|---|---|
| Data Acquisition and Exploratory Data Analysis (EDA) | 20% |
| Code Quality, Documentation and GitHub Collaboration | 20% |
| LLM Integration and Usage | 20% |
| Streamlit App Functionality and Design | 20% |
| Presentation | 20% |

### 1.4 Submission

Submit the GitHub repository link and the Streamlit app URL (if hosted online);
include the project report (PDF or Word) in both the repository and the
submission portal.

---

## 2. Requirement → Orbix mapping

Orbix is built in three phases so that each marked requirement is satisfied by
verified work rather than by an impressive-looking shell.

* **Phase 1 (current):** real orbital data acquisition, SGP4 propagation,
  interactive 3D Earth with selection, trajectories and time control.
* **Phase 2:** EDA, descriptive statistics, correlations, missing data and
  outlier treatment, in-app insights.
* **Phase 3:** meaningful LLM integration (natural-language orbital analysis and
  explanations, grounded in the real computed data).

| Assignment requirement | How Orbix satisfies it | Phase | Status at end of Phase 1, Step B |
|---|---|---|---|
| Connect to ≥1 external API for real data | CelesTrak GP API (OMM/JSON) via `src/api/celestrak.py`; live ISS + group queries verified | 1 | **Done** (ISS 25544, GROUP=STATIONS; GPS-OPS in Step F) |
| Document source and structure of the data | This document plus the module docstring in `src/api/celestrak.py`: endpoint syntax, OMM field list, units, JSON layout, usage policy | 1 | **Done** |
| Data retrieval and storage logic in a module | `CelestrakClient` with 2-hour TTL cache, immutable raw snapshots in `data/snapshots/`, `manifest.json` recording retrieval timestamps, SHA-256, record counts and newest OMM epoch | 1 | **Done** (54 automated tests) |
| Descriptive statistics and visualisations | Pandas summaries of the element population (altitude/inclination/period distributions) as Plotly charts beside the 3D globe | 2 | Deferred (Phase 2) |
| Outliers and missing data | Explicit handling of legitimately absent OMM fields (`OBJECT_NAME`, `BSTAR`, `OBJECT_ID`) plus statistical outlier detection on derived quantities | 2 | Deferred (Phase 2). Groundwork: the API layer rejects non-physical elements instead of silently coercing them |
| Correlation analysis between key variables | Correlation matrix over derived orbital metrics (inclination vs altitude, period vs semi-major axis, B* vs decay) | 2 | Deferred (Phase 2) |
| Insights and summaries displayed in Streamlit | Summary panels grounded in the user's filtered satellite set | 2 | Deferred (Phase 2) |
| LLM integration used meaningfully | Assistant receives the *actual* propagated figures (selected satellite, altitude, velocity, period, element age) and explains them, answers "where is X right now / over which region", and produces mission summaries | 3 | Deferred (Phase 3). No LLM code exists yet, by design |
| Streamlit app: clean interface | Dark aerospace mission-control theme driven by `.streamlit/config.toml` plus a custom CSS layer; custom HTML panels instead of default widgets; Streamlit's own chrome hidden | 1 | **Done** (Step D) |
| Streamlit app: user interaction with data | Rotate/zoom 3D Earth, hover readouts, click-to-select event back to Python, data-source toggles | 1 | **Done for one satellite** (Step D); time playback and multi-satellite selection in Steps E-F |
| Streamlit app: displays EDA results | EDA panel built in Phase 2 | 2 | Deferred (Phase 2) |
| Streamlit app: LLM integrated in the app | Chat/summary panel built in Phase 3 | 3 | Deferred (Phase 3) |
| Streamlit app: error handling, documentation, user guidance | Typed exceptions surfaced as styled notices with actionable advice, explicit LIVE/CACHED/SNAPSHOT provenance chip, offline mode, loading veil, sidebar explanation of the data and propagation chain | 1 | **Done** (Step D) |
| Modular, documented, readable code | `src/api`, `src/orbital`, `src/data`, `components/globe`, `scripts`, `tests`; type hints, docstrings, no business logic in `app.py` | all | **In progress** (API layer complete) |
| Version control (Git/GitHub) | Git repository with per-step commits; committed only after author approval | all | **Done, pending approval to commit** |
| `README.md` (purpose, setup, API/data details, run instructions) | `README.md` at repository root | all | **In progress** (data source and test instructions present; app instructions added at Step D) |
| GitHub repository deliverable | This repository, including small CelesTrak snapshots for reproducibility | all | **In progress** |
| Streamlit app deliverable (local or Cloud) | Runs locally; Cloud-compatible (relative paths, no secrets, no local database) | 1 | Not yet runnable (Steps D–E) |
| Project report (2–3 pages + screenshots) | `docs/project_report.md` drafted in Phase 3 from the decisions log | 3 | Deferred |
| Live demo (≤15 min) | Demo script built once Phases 1–3 are complete | 3 | Deferred |

---

## 3. Phase 1 scope

### In scope

1. **Data acquisition (Step B — complete).** CelesTrak GP/OMM client with
   validation, caching, snapshot persistence, offline fallback and freshness
   reporting. It never fabricates data.
2. **Propagation (Step C - complete).** Skyfield/SGP4 propagation of OMM elements
   to any UTC timestamp: geodetic latitude/longitude/altitude on WGS-84, ECEF and
   GCRS Cartesian positions, inertial speed, orbital period, and element age with
   staleness warnings. Verified against a direct `sgp4` calculation and a
   hand-written TEME->ECEF rotation (agreement 0.003 m).
3. **3D visualisation (Steps D-E).** Streamlit custom component embedding Globe.gl
   (Three.js): NASA Blue Marble Earth with atmosphere, ISS marker at its true
   altitude, Earth-fixed orbit track for one period, drag/zoom camera, mission-control
   information panel, LIVE/CACHED/SNAPSHOT provenance chip, offline mode, loading and
   error states. **Step D complete**; Step E adds time playback and richer selection.
4. **Additional groups (Step F).** GPS-OPS and a bounded Starlink subset once the
   ISS path is verified end to end, with rendering performance work.

### Explicitly out of scope for Phase 1

* Any EDA, statistics, correlation, missing-data or outlier analysis (Phase 2).
* Any LLM or generative-AI functionality (Phase 3).
* Databases, paid infrastructure, user accounts, or persistent server state.
* Fake, synthetic or hand-written orbital data of any kind.

---

## 4. Known gaps and risks against the brief

| Risk | Detail | Mitigation |
|---|---|---|
| "Teamwork"/"each team member" wording | The PDF assumes a multi-member team with per-member commit history. Orbix is a solo project, so the report must state this clearly and the commit history has one author. | State it explicitly in the report's reflection section; keep a clean, meaningful solo commit history. |
| 20% of the mark is the LLM component | Nothing in the repository scores those marks until Phase 3. | Phase 3 is planned, and Phase 1 deliberately structures the propagated data so the LLM reasons over real numbers rather than invented context. |
| 20% of the mark is the presentation | A 15-minute live demo needs a script and a fallback. | Snapshot caching means the demo still works if CelesTrak is unreachable during class (offline mode). |
| CelesTrak rate limiting | Since 2026-03-26 CelesTrak enforces one download per 2-hour update; repeats return HTTP 403, and persistent abuse gets an IP firewalled. | 2-hour TTL cache, no automatic retries, HTTP 403 treated as "reuse cache", snapshots committed to the repo. |
| Data latency | GP element sets update 2–3 times per day; the OMM epoch is usually several hours old, so propagated positions carry metre-to-kilometre error. | The freshness banner shows both retrieval age and element-epoch age; the app never implies real-time tracking. |
| Streamlit Cloud filesystem | Streamlit Community Cloud has an ephemeral filesystem, so runtime snapshots vanish on redeploy. | Committed snapshots provide the baseline; the client degrades to "network, or newest saved snapshot" and never crashes. |
| TLE-compatible tooling expectations | Most tutorials use legacy TLE; Orbix uses OMM/JSON to stay compatible with 6- and 9-digit catalog numbers. | Skyfield 1.55 supports `EarthSatellite.from_omm()`; `GpElement.as_omm()` emits all 17 OMM keywords for it. |

---

## 5. Compliance checklist

| Deliverable | Requirement | Status |
|---|---|---|
| 1. GitHub repository | Source code, data files, documentation | In progress — API layer + docs + snapshots committed after approval |
| 1. GitHub repository | Meaningful commit history | In progress — one commit per verified step |
| 2. Streamlit app | Runs locally or on Streamlit Cloud | Not started (Step D) |
| 2. Streamlit app | Demonstrates all required functionalities | Not started (Steps D–F, Phases 2–3) |
| 3. Project report | 2–3 pages, objectives, methodology, results, screenshots, reflection | Deferred (Phase 3) |
| 4. Live demo | ≤15 minutes, in front of the class | Deferred (Phase 3) |
| Submission | Repository link + app URL + report | Deferred (end of Phase 3) |


