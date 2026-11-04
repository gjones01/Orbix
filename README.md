# Orbix

**Interactive 3D satellite visualization and orbital intelligence.**

Orbix is a Streamlit application that pulls *real* orbital data from CelesTrak,
propagates it with SGP4, and renders an interactive three-dimensional Earth
surrounded by satellites whose positions users can watch evolve over simulation
time. The goal is a professional aerospace mission-control feel rather than a
generic dashboard, without ever compromising scientific correctness.

Orbix is a solo final project for the *Masters of Data Science — Programming for
Data Science* capstone. The assignment mapping (what is required, what each phase
delivers) lives in [`docs/assignment_requirements.md`](docs/assignment_requirements.md).

> **Current status: Phase 1, Step B complete.** Real CelesTrak data acquisition,
> validation, caching and snapshotting are implemented and tested (54 tests).
> Orbital propagation and the 3D interface are the next steps; see
> [Roadmap](#roadmap).

---

## Data source

| Item | Detail |
|---|---|
| Provider | [CelesTrak](https://celestrak.org), operated by Dr. T.S. Kelso |
| Service | General Perturbations (GP) — *Current Data* |
| Format used | `FORMAT=JSON` — CCSDS **OMM** (Orbit Mean-elements Message) keywords |
| Endpoint | `https://celestrak.org/NORAD/elements/gp.php?{QUERY}=VALUE&FORMAT=JSON` |
| Query selectors | `CATNR` (catalog number), `GROUP` (satellite group), `INTDES`, `NAME`, `SPECIAL` — uppercase only |
| Documentation | <https://celestrak.org/NORAD/documentation/gp-data-formats.php> |
| Usage policy | <https://celestrak.org/NORAD/documentation/gp-data-formats.php> (rate-limiting section) |
| Example (ISS) | <https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=JSON> |
| Example (stations) | <https://celestrak.org/NORAD/elements/gp.php?GROUP=STATIONS&FORMAT=JSON> |

### Structure of the data

The response is a JSON **array of OMM objects** (a single-element array even for
`CATNR` queries), for example the live ISS response captured on 2026-11-04:

```json
[
  {
    "OBJECT_NAME": "ISS (ZARYA)",
    "OBJECT_ID": "1998-067A",
    "EPOCH": "2026-11-04T08:52:07.404096",
    "MEAN_MOTION": 15.49173979,
    "ECCENTRICITY": 0.0007186,
    "INCLINATION": 51.6329,
    "RA_OF_ASC_NODE": 326.3987,
    "ARG_OF_PERICENTER": 346.7494,
    "MEAN_ANOMALY": 13.3304,
    "EPHEMERIS_TYPE": 0,
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 25544,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 59348,
    "BSTAR": 0.00010236,
    "MEAN_MOTION_DOT": 5.228e-5,
    "MEAN_MOTION_DDOT": 0
  }
]
```

| Field | Meaning | Unit |
|---|---|---|
| `OBJECT_NAME` | Common name (may be blank for analyst objects) | — |
| `OBJECT_ID` | International designator | `yyyy-nnnP` |
| `EPOCH` | Time the elements were fitted (ISO-8601, UTC) | UTC |
| `MEAN_MOTION` | Mean revolutions per day | rev/day |
| `ECCENTRICITY` | Orbital eccentricity | — |
| `INCLINATION` | Orbital inclination | degrees |
| `RA_OF_ASC_NODE` | Right ascension of the ascending node | degrees |
| `ARG_OF_PERICENTER` | Argument of perigee | degrees |
| `MEAN_ANOMALY` | Mean anomaly at epoch | degrees |
| `EPHEMERIS_TYPE` | Ephemeris model (`0` = SGP4) | — |
| `CLASSIFICATION_TYPE` | `U`nclassified / `C`onfidential / `S`ecret | — |
| `NORAD_CAT_ID` | Catalog number | integer |
| `ELEMENT_SET_NO` | Element set sequence number | integer |
| `REV_AT_EPOCH` | Revolution count at epoch | integer |
| `BSTAR` | Drag term | 1/Earth radii |
| `MEAN_MOTION_DOT`, `MEAN_MOTION_DDOT` | First/second mean-motion derivatives | rev/day², rev/day³ |

**CelesTrak provides orbital *elements*, not positions.** Every Cartesian
position the app displays is computed locally by propagating these elements to
the requested UTC timestamp.

### How Orbix respects the CelesTrak usage policy

* GP data is refreshed **once every 2 hours**, so the client's cache TTL is
  exactly 2 hours and a cached snapshot causes **no** network traffic.
* Since 2026-03-26 CelesTrak allows **one download per update**; a repeat request
  returns HTTP 403 "GP data has not updated since your last successful
  download". Orbix treats that as a cache hit, not an error.
* **No automatic retries** on 403/404/5xx or timeouts — repeated error requests
  get an IP firewalled.
* Only the groups Orbix actually needs are requested; bulk groups
  (`active`, Starlink) are avoided.
* Every successful payload is saved verbatim so it can be reused offline.

---

## Repository layout

```text
Orbix/
├── app.py                          # Streamlit entry point (Step D)
├── src/
│   ├── api/celestrak.py            # CelesTrak GP/OMM client, cache, snapshots
│   ├── orbital/propagation.py      # Skyfield SGP4 propagation (Step C)
│   └── data/processing.py          # element cleaning / tabular prep (later)
├── components/globe/               # Three.js / Globe.gl front-end (Step D)
├── scripts/verify_celestrak_api.py # Step B acceptance check (CLI)
├── tests/                          # pytest suite + real-data fixtures
├── data/snapshots/                 # saved CelesTrak payloads + manifest.json
├── docs/
│   ├── assignment_requirements.md  # requirement → feature mapping
│   └── technical_decisions.md      # decisions, evidence, limitations
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
└── README.md
```

---

## Setup

Requires **Python 3.11+** (verified on 3.13.5).

```bash
git clone <repository-url>
cd Orbix

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

No API keys or credentials are required: the CelesTrak GP API is public. Orbix
does not read secrets from `.env` for data access and never hardcodes keys.

---

## Verifying the data pipeline

The Step B acceptance check performs one CelesTrak query, validates the OMM
structure, saves a snapshot, and prints a verification summary:

```bash
python scripts/verify_celestrak_api.py                 # ISS (NORAD 25544)
python scripts/verify_celestrak_api.py --show-raw      # + raw OMM record
python scripts/verify_celestrak_api.py --group STATIONS
python scripts/verify_celestrak_api.py --offline       # snapshots only
python scripts/verify_celestrak_api.py --refresh       # ignore the 2h cache
```

Exit code `0` means real data was retrieved, validated and stored. Non-zero means
the request failed and nothing was fabricated.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite is hermetic: HTTP is replaced by injected fakes, orbital fixtures are
real captured CelesTrak payloads, and the clock is controllable so cache-expiry
behaviour is deterministic. Expected result: **54 passed**.

---

## Roadmap

| Step | Scope | Status |
|---|---|---|
| A | Repository inspection and environment audit | done |
| B | CelesTrak API client, snapshots, tests | **done** |
| C | Skyfield SGP4 propagation: lat/lon/altitude, TEME/ECEF, reference-case tests | next |
| D | Streamlit + 3D Earth (Three.js/Globe.gl) with the ISS at its propagated position | pending |
| E | Selection, information panel, orbit trajectory, time slider/playback, freshness badge | pending |
| F | GPS-OPS and a bounded Starlink subset, rendering performance | pending |
| Phase 2 | EDA: statistics, correlations, missing data, outliers, in-app insights | deferred |
| Phase 3 | LLM-grounded orbital analysis and natural-language explanations | deferred |

---

## Data integrity statement

Orbix will not display invented orbital data. If CelesTrak is unreachable and no
saved snapshot exists, the application reports the failure instead of drawing
satellites at made-up positions. Every rendered position is derived from a real
OMM element set whose retrieval time and epoch are shown in the interface.

