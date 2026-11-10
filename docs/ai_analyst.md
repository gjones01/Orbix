# Ask Orbix — Phase 3

Ask Orbix is an optional conversational analyst inside Analysis. Submit a question,
choose a suggested question, or click **Find interesting patterns**. It explains the
loaded satellite data; it does not browse, execute code, or operate the globe.
The existing deterministic discoveries, statistics, selection, and playback remain intact.

## Configuration

No provider is selected by default and no account or paid service is provisioned.
Use an existing OpenAI-compatible **Chat Completions** endpoint. Set these in the
server environment, or as flat keys in `.streamlit/secrets.toml` (git-ignored):

```toml
ORBIX_LLM_BASE_URL = "https://api.deepseek.com"
ORBIX_LLM_MODEL = "deepseek-flash"
ORBIX_LLM_API_KEY = "your-api-key"
ORBIX_LLM_TOKEN_PARAMETER = "max_tokens"
ORBIX_LLM_MAX_TOKENS = 2048
```

The base URL is the API root; Orbix appends `/chat/completions`. Environment values
override secrets. `.env` files are **not** automatically loaded. Restart Streamlit
after changing server environment variables. Never commit real credentials.

For a DeepSeek-compatible endpoint, use its API root and configured model identifier.
The local installation is now configured for `deepseek-flash` at `https://api.deepseek.com`, with credentials in the ignored secrets file. For models
requiring `max_completion_tokens`, set `ORBIX_LLM_TOKEN_PARAMETER` accordingly; Orbix
does not guess or retry with another parameter. The adapter follows the
[OpenAI Chat Completions contract](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
Responses must support system messages and return the requested JSON in message content.
Models which cannot finish a response within the configured response budget (default 1,200 tokens) may be unsuitable.
HTTP is accepted only for localhost endpoints; remote endpoints require HTTPS.

Without configuration, Ask Orbix shows setup guidance and disables requests. All
ordinary dashboard/globe features work. Offline snapshot mode avoids CelesTrak
downloads; an explicitly submitted question still contacts the configured model
endpoint. Use a local compatible endpoint for fully offline AI.

## Grounding and scope

`src/analyst/context.py` reads the Analysis DataFrame, never a separate catalogue.
Each context has a SHA-256-based identifier over the actual frame and position anchor,
timestamp, selected groups, effective answer groups, available/missing groups, and
explicit limitations. Counts use unique NORAD records and primary constellation
assignment, matching the dashboard. The question router selects evidence for
comparisons, altitude/inclination distributions, outliers, quality, correlations,
individual objects, numerical thresholds, rankings, raw API fields, or broad patterns. Every request also includes field definitions and provenance; compact summaries cover the available numerical fields.

Python computes group counts/shares, descriptive statistics, missing counts and
element freshness using `src/analysis/stats.py`. Altitude flags reuse `overview()`:
group **and** regime strata, 1.5×IQR **OR** absolute modified z >3.5, at least four
valid measurements, MAD=0 disabling only the z criterion. Fixed 25 km altitude bins
(six most populated per group) give limited distribution evidence; they are not proof
of shells, operational status or deployment history. Spearman evidence includes
pairwise complete counts and pooled-versus-within summaries. Undefined values become
JSON null, never zero. No scientific thresholds or existing statistics were changed.

By default answers use **Groups to compare**. Explicitly naming another loaded group
or a NORAD ID can override that scope for the answer; its scope is displayed with the
answer. Exact names resolve to stable IDs; at most five individual records are sent.
“The selected satellite” uses the same `orbix_selected` ID as the globe. An absent
group/ID is reported, never invented. The question still reaches the model, which can answer supported parts and explain concepts. Load more groups through the sidebar or the explicit load button for their numerical results.
Short pronoun follow-ups can reuse the preceding same-filter question for retrieval.
Unrecognized phrasing receives general summaries, so explicit group names and NORAD
IDs give the most reliable retrieval.

Questions comparing a named group with “the others” include the other groups selected
in Analysis. If a requested group is missing, Ask Orbix identifies it and offers a
**Load [group]** button. The provider receives the available evidence plus an explicit missing-group notice; it must not invent a numerical comparison for the missing group.
Starlink is enabled by default in a fresh session. Loading it uses the existing catalogue loader;
it does not send an AI request. The question stays in the input so it can be submitted
again once loading finishes. Enabled groups with no records point to the Globe data report.

## Requests, privacy and limits

Only an explicit submission sends a request. There are no background model calls,
tool loops, retries, redirects, provider fallbacks or extra dependencies. Deterministic
contexts are cached (24 entries); answers are **not** shared or cached across users.

- Questions: at most 1,600 characters, validated before requesting.
- Evidence: at most 26,000 serialized characters; optional sections are removed whole
  if necessary and omissions are marked. Oversized contexts ask for a narrower scope.
- Response budget: 1,200 tokens by default; `ORBIX_LLM_MAX_TOKENS` accepts 128?4096 (this configuration uses 2048); connection timeout 5 seconds, read timeout 35 seconds.
- Session history: eight turns maximum; at most two same-filter turns go to the model.
- A submission is consumed before network I/O; a rerun cannot resend a consumed event.
  Clicking again is a new explicit request, including after a failure.

The provider receives the question, aggregate statistics, field definitions, up to five
requested individual records with all Analysis columns, bounded ranking/threshold examples,
the highest-altitude and oldest-element records, provenance and bounded conversation
history. It also receives up to five original CelesTrak OMM records from the loaded scene
for requested/ranked objects or labelled examples. These records preserve source values
and absent fields; they are not reconstructed with propagation defaults. It never receives the full catalogue, keyframes, local files or credentials
in the prompt. The API key travels only in the request's Authorization header.
Provider data-retention policies still apply to submitted content.

Conversation history is held only in the Streamlit session. Changing filters preserves
earlier answers with a visible “Earlier answer” label; previous-filter turns are not
sent as conversational context. Changing the snapshot clears the conversation with an
explanation. Ordinary reruns, unit changes, camera movement and playback do not make
requests or refresh the snapshot. Clearing history is explicit.

## Transparency and limitations

Responses contain one to five short findings, each with an explanation, caveat and
references to supplied evidence keys. The patterns action requests three to five
findings when supported. Malformed JSON, missing fields and invented evidence references
are rejected. Each answer shows its snapshot/time/groups and expandable computed evidence
and token usage when provided. Untrusted text is rendered as text, not HTML or external
images. No model output can change filters, execute commands or trigger other requests.

Validation of evidence references **cannot prove every sentence or number in generated
prose is correct**. The model can still misinterpret evidence or ignore instructions;
users should compare claims with the visible Python evidence. System instructions allow orbital explanations and general scientific background, labelled
as such, while requiring observations, interpretations and unknowns to remain distinct.
A question is not rejected merely for asking why or naming an API field. General
background cannot establish a particular operator?s intent or a satellite?s health. There is no operational telemetry, mission history, collision
analysis, future event retrieval or live position measurement. SGP4 estimates refer to
the snapshot anchor; element age is measured against that anchor, not retrieval time.
All AI evidence retains scientific km/degrees even if the dashboard display uses miles.

Provider errors are sanitized; raw error bodies and secrets are never displayed or logged
by the adapter. Missing credentials, timeouts, quotas, invalid output and empty scopes
leave the rest of Orbix usable. There is no silent substitution with a paid provider.

## Verification and course requirement

`tests/test_analyst.py` and `tests/test_analyst_ui.py` cover computed context, explicit
group/ID retrieval, outlier parity, missing values, filters/snapshots, bounded history,
request construction, errors and duplicate prevention. External LLM HTTP calls are mocked.

```powershell
python -m pytest -q -p no:cacheprovider --basetemp=C:\Orbix\test-temp-ai-check
python scripts/verify_analyst_ui.py
python scripts/verify_analysis_selection.py
```

The analyst browser script runs Streamlit against offline orbital data and a local HTTP
mock. It verifies browser submission → computed context → HTTP request → visible answer,
NORAD lookup, persistence across reruns, clear conversation, and existing selection.
Screenshots with `ask-orbix-mock` in their filename are explicitly mock-provider evidence.
They are **not** evidence of a real LLM response. DeepSeek credentials are now configured locally. The sandbox network attempt could not reach the provider, and automatic approval review rejected the external verification request pending explicit authorization for credential use, data transfer and potential cost. Live LLM verification remains outstanding.

Verification on 2026-11-06: **198 tests passed**, including 29 new analyst tests.
The real browser/HTTP mock flow passed with two explicit requests, no extra requests
on unrelated reruns, and no severe console errors. Desktop and laptop screenshots
were inspected. The all-five-group context contained 11,982 satellites in about
17,000 serialized characters, without omitting an evidence section. The separate
globe browser regression passed farthest/individual/unusual selections, hidden-group
reveal, repeat selection, camera preservation on ordinary reruns and continued playback.

This implements the assignment's meaningful LLM integration: an actual configurable API
request interprets real Python-computed EDA and answers dataset-specific questions in
Streamlit. A configured successful live request is still needed for the final demonstration.
The project report and class presentation remain separate deliverables.

### Missing-group recovery verification

The exact question ?Why are the starlink satellites so much lower altitude than the others??
was verified through Streamlit tests and a real browser against a local HTTP mock.
The app identifies missing Starlink data, loads it on explicit request, preserves the
question, and includes the other selected groups on resubmission. Loading and unrelated
reruns make no model request. The browser received two mock responses (comparison and
NORAD lookup), with 11,982 satellites in the comparison and no severe console errors.
The full regression suite passed: **205 tests**. The browser harness explicitly isolates
its provider configuration from local credentials. A real DeepSeek response remains unverified.

### Broader data access and explanations (2026-11-08)

The earlier strict summary-only instruction and missing-data submission block were removed.
Ask Orbix now answers from both derived/displayed values and original CelesTrak records.
Python still computes aggregate numbers; the model interprets them and can provide labelled
general orbital background. A bare filter does not restrict explicit satellite/group lookups;
with no Analysis groups selected, a general question uses all loaded groups.

The context supports all numeric Analysis fields and their OMM aliases, full individual
rows, top/bottom five rankings, and a single explicit above/below/at-least/at-most condition.
Threshold evidence reports its parsed field, units, count, denominator and missing count;
it does not claim to evaluate arbitrary compound queries. Measurement values such as
10,000 km are no longer treated as NORAD IDs. Unsupported calculations remain limitations;
the model is never given arbitrary code execution or unrestricted endpoint access.
Raw values come from the exact API records already loaded for the globe, preserving
snapshot consistency and CelesTrak caching. No extra API download or model planning loop
is introduced. Bounded examples are labelled and optional context is trimmed as whole
sections when needed; directly requested correlation evidence is retained.

### Editing the response tone

`prompts/analyst_style.md` is the runtime style guide. The provider reads it on every
new submitted question and appends it to the system instructions. It is deliberately
not a Codex `SKILL.md`: a coding-agent skill does not automatically control the model
inside this app. Edit this Markdown file to change the analyst's tone without changing
Python. Existing conversation answers are not rewritten; submit a new question or clear
the conversation to see the new style. Core grounding/security and JSON formatting rules
remain in `src/analyst/provider.py`.

The default audience has no orbital-mechanics background. The guide asks for a direct
answer first, 100?180 words, short paragraphs, two or three useful rounded numbers,
plain-English terms and no unexplained acronyms. Numerical ratios must come from supplied
evidence, not model arithmetic. Caveats are stated once when useful; repeated caveat
fields can be empty and are not rendered. Explicit requests for technical detail override
the default level of explanation. Patterns have a separate concise three-to-five-finding
format. The computed evidence stays available in the expandable panel.

On 2026-11-08 the user reported a successful model response and supplied a screenshot.
Automated provider verification still uses mocks rather than a direct live API smoke test.
The broader retrieval browser check passed three explicit mock requests, including a
partial-data explanation, a loaded-group comparison and original API fields for NORAD 25544,
with no severe browser errors. Prompt/style tests verify that file edits reach the actual
request and empty repeated caveats are accepted; they do not guarantee every model response
will follow the style perfectly.

Final regression verification after the response-style changes: **214 tests passed**.
