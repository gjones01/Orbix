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

> **Current status: Phase 1, Step C complete.** Real CelesTrak data acquisition,
> caching and snapshotting (Step B) plus Skyfield/SGP4 propagation to WGS-84
> geodetic, Earth-fixed (ECEF) and inertial (GCRS) coordinates (Step C) are
> implemented and tested (**83 tests**). The 3D interface is next; see
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

## Orbital propagation

`src/orbital/propagation.py` turns those element sets into positions with
Skyfield's SGP4 implementation, which is the corrected algorithm from Vallado,
Crawford & Hujsak, *Revisiting Spacetrack Report #3*.

One call returns the same physical position in three coordinate systems, because
mixing them up is the classic orbital-visualisation bug:

| `OrbitalState` field | Coordinate system | Rotates with Earth | Use it for |
|---|---|---|---|
| `latitude_deg`, `longitude_deg`, `altitude_km` | **WGS-84 geodetic** (`a` = 6378.137 km, `f` = 1/298.257223563) | surface | readouts, sub-satellite point |
| `ecef_km` | **ITRS / ECEF** — `x` at 0° longitude on the equator, `y` at 90°E, `z` at the north pole | **yes** | satellite markers on the Three.js globe |
| `gcrs_km` | **GCRS / ICRS** geocentric inertial | no | orbit planes, inertial geometry |
| `native_sgp4_state().teme_km` | **TEME** — SGP4's native frame | no | cross-checking only, never rendered |

Also returned: `speed_km_s` (inertial), `radius_km`, `period_minutes` (from the
mean motion), `inclination_deg`, the element `epoch`, the `element_age` at the
requested instant, and any staleness `warnings`.

```python
from datetime import datetime, timezone
from src.api.celestrak import CelestrakClient
from src.orbital import propagate

iss = CelestrakClient().fetch_by_catalog_number(25544).elements[0]
state = propagate(iss, datetime(2026, 11, 4, 9, 22, 7, tzinfo=timezone.utc))

state.latitude_deg, state.longitude_deg, state.altitude_km   # 44.751, -89.176, 426.71
state.ecef_km                                                # (-4839.62, 4768.18, 69.57)
state.gcrs_km                                                # inertial
state.speed_km_s                                             # 7.659
```

Verified behaviour:

* ISS at its 2026-11-04 epoch: **415.13 km** altitude, **7.6659 km/s**, period
  **92.95 min**, inclination **51.63°**.
* The independent `sgp4` library and a from-scratch TEME→ECEF rotation written out
  of the IAU-82 GMST expression reproduce the pipeline's Earth-fixed position to
  **0.003 m**.
* Earth-fixed and inertial vectors always share the same length, and the rotation
  between them advances at the sidereal rate (15.041°/h) — the tests assert both,
  so a frame mix-up cannot pass silently.
* Timezone-naive timestamps are **rejected** rather than guessed; elements more
  than 3.5 days from the requested time warn, and more than 30 days refuse.

```bash
python scripts/verify_propagation.py                  # ISS at its element epoch
python scripts/verify_propagation.py --when now --offsets 0,15,30,45
python scripts/verify_propagation.py --group STATIONS --limit 4
```

No network access is needed for propagation: Skyfield's built-in Earth-orientation
tables are used by default (the IERS `finals2000A.all` variant differs by 72 m and
is opt-in), and no JPL ephemeris download is required.

---

## Repository layout


```text
Orbix/
├── app.py                          # Streamlit entry point (Step D)
├── src/
│   ├── api/celestrak.py            # CelesTrak GP/OMM client, cache, snapshots
│   ├── orbital/propagation.py      # SGP4 -> geodetic/ECEF/GCRS positions
│   └── data/processing.py          # element cleaning / tabular prep (later)
├── components/globe/               # Three.js / Globe.gl front-end (Step D)
├── scripts/
│   ├── verify_celestrak_api.py     # Step B acceptance check (CLI)
│   └── verify_propagation.py       # Step C acceptance check (CLI)
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
behaviour is deterministic. Propagation tests run entirely offline against
Skyfield's built-in time tables. Expected result: **83 passed**.

---

## Roadmap

| Step | Scope | Status |
|---|---|---|
| A | Repository inspection and environment audit | done |
| B | CelesTrak API client, snapshots, tests | **done** |
| C | Skyfield SGP4 propagation: geodetic/ECEF/GCRS, reference cross-check, tests | **done** |
| D | Streamlit + 3D Earth (Three.js/Globe.gl) with the ISS at its propagated position | next |
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

