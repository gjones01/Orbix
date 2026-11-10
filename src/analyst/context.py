"""Deterministic retrieval over the same snapshot used by Analysis and Globe."""
from __future__ import annotations

import hashlib
import json
import re

import numpy as np
import pandas as pd

from src.analysis import stats
from src.analysis.overview import overview
from src.analysis.provenance import COLUMNS
from src.api.catalog import GROUP_SPECS

MAX_QUESTION = 1600
MAX_CONTEXT = 26000
LABELS = {s.id: s.label for s in GROUP_SPECS}
VARIABLES = {
    "altitude_km": ("altitude", "height"),
    "inclination_deg": ("inclination",),
    "period_min": ("period",),
    "eccentricity": ("eccentricity", "eccentric"),
    "bstar": ("bstar", "b*", "drag"),
    "mean_motion_rev_day": ("mean motion",),
    "semi_major_axis_km": ("semi-major", "semimajor", "semi major"),
    "element_age_hours": ("element age",),
    "raan_deg": ("raan", "ascending node"),
    "arg_pericenter_deg": ("pericentre", "pericenter", "argument of perigee"),
    "mean_anomaly_deg": ("mean anomaly",),
    "mean_motion_dot": ("mean motion derivative", "mean_motion_dot"),
    "rev_at_epoch": ("revolutions at epoch", "rev_at_epoch"),
    "element_set_no": ("element set number", "element_set_no"),
    "perigee_radius_km": ("perigee radius",),
    "apogee_radius_km": ("apogee radius",),
    "latitude_deg": ("latitude",),
    "longitude_deg": ("longitude",),
}


def requested_variables(text: str, frame: pd.DataFrame) -> list[str]:
    """Accept UI names, Python columns and the original API field names."""
    found = []
    for spec in COLUMNS:
        if spec.name not in frame or not pd.api.types.is_numeric_dtype(frame[spec.name]) or spec.name == "norad_id":
            continue
        terms = (spec.name, spec.label.lower(), spec.source.removeprefix("OMM ").lower(),
                 *VARIABLES.get(spec.name, ()))
        if any(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text) for t in terms if t):
            found.append(spec.name)
    return found


def fit_context(context: dict) -> dict:
    """Remove optional sections whole, keeping the question's direct evidence."""
    optional = ["record_examples", "catalogue_statistics", "altitude_bins"]
    if "correlations" not in context.get("intents", []):
        optional.append("correlations")
    for key in optional:
        if len(json.dumps(context, ensure_ascii=True)) > MAX_CONTEXT:
            if context["evidence"].pop(key, None) is not None:
                context.setdefault("omitted_evidence", []).append(key)
    if len(json.dumps(context, ensure_ascii=True)) > MAX_CONTEXT:
        raise ValueError("This context is too large. Ask about fewer groups or one satellite.")
    return context


def attach_raw_records(context: dict, scene) -> dict:
    """Read original API records from the loaded scene; no network or default-filled OMM."""
    import copy
    result = copy.deepcopy(context)
    evidence = result["evidence"]
    candidates = evidence.get("satellites", []) or evidence.get("ranked_records", {}).get("records", [])
    candidates = candidates or evidence.get("record_examples", {}).get("records", [])
    raw = []
    for row in candidates[:5]:
        element = scene.element(row["norad_id"])
        if element is not None:
            raw.append({"norad_id": int(row["norad_id"]), "omm": dict(element.raw)})
    evidence["raw_api_records"] = {"source": "Original CelesTrak OMM records underlying this snapshot; omitted fields remain absent, no defaults added.",
                                   "retrieved_at": scene.catalog.retrieved_at.isoformat(),
                                   "coverage": "Requested/ranked records, or at most five explicitly labelled examples; not the entire catalogue.",
                                   "records": raw}
    return fit_context(result)
METHOD = (
    "Altitude flags compare within constellation AND orbital regime: outside "
    "Q1 - 1.5*IQR or Q3 + 1.5*IQR OR absolute modified z = "
    "0.6745*(value-median)/MAD > 3.5. Fewer than 4 valid values: unassessed. "
    "MAD=0 disables only modified z. Flags never remove records or prove failure."
)


def snapshot_id(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256(str(frame.attrs.get("anchor")).encode())
    digest.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
    return digest.hexdigest()[:20]


def records(frame: pd.DataFrame) -> list[dict]:
    # pandas serialisation converts NaN/NaT to JSON null, including missing measurements.
    return json.loads(frame.to_json(orient="records", date_format="iso", double_precision=6))


def route(question: str) -> set[str]:
    text = question.lower()
    intents = set()
    for intent, words in {
        "outliers": ("outlier", "unusual", "flag", "anomal"),
        "quality": ("reliab", "fresh", "age", "missing", "quality", "accur", "stale"),
        "correlations": ("correl", "relationship", "relat", "associat"),
        "distribution": ("altitude", "height", "farther", "farthest", "distribution", "inclination"),
        "comparison": ("compar", "versus", " vs ", "differ"),
        "patterns": ("pattern", "interesting", "discover"),
    }.items():
        if any(word in text for word in words):
            intents.add(intent)
    return intents or {"summary"}


def suggestions(groups: list[str]) -> list[str]:
    items = ["How reliable are the positions in this snapshot?",
             "Why are satellites flagged as altitude outliers?"]
    if {"starlink", "oneweb"} <= set(groups):
        items.insert(0, "How do Starlink and OneWeb compare?")
    elif len(groups) > 1:
        items.insert(0, "Which selected group operates farthest from Earth?")
    return items


def build_context(frame: pd.DataFrame, selected: tuple[str, ...], question: str,
                  selected_norad: int | None = None, previous_question: str = "") -> dict:
    """Bounded evidence; explicit groups/IDs may reach outside the Analysis filter.

    A short referential follow-up reuses the previous question for retrieval only.
    Neither user text nor model output is executable, and no network is used here.
    """
    if not question.strip() or len(question) > MAX_QUESTION:
        raise ValueError(f"Enter a question of 1–{MAX_QUESTION} characters.")
    text = question.lower()
    if re.search(r"\b(they|them|those|that|it|these|their)\b", text):
        text += " " + previous_question[:MAX_QUESTION].lower()
    available = set(frame["constellation"].astype(str))
    mentioned = [key for key, label in LABELS.items()
                 if any(re.search(r"\b" + re.escape(term) + r"\b", text)
                        for term in (key, label.lower(), "iss" if key == "stations" else label.lower()))]
    groups = mentioned or list(selected) or sorted(available)
    # A named group compared with "the others" needs both sides of the comparison.
    # Keep missing named groups in scope so the UI can request their data explicitly.
    if mentioned and re.search(r"\b(others|other (?:selected )?(?:groups|satellites|constellations)|the rest)\b", text):
        groups = list(dict.fromkeys(mentioned + (list(selected) if len(selected) > 1 else sorted(available))))
    if re.search(r"\b(all|entire|whole) (loaded |available )?(groups|dataset|catalogue|catalog|satellites)\b", text):
        groups = sorted(available)
    # A measurement such as 10000 km is not a NORAD ID.
    ids = {int(n) for n in re.findall(r"\b(?:norad(?:\s+(?:cat(?:alog)?(?:ue)?|id|number))?|satellite|catnr|about|describe|explain|object)\s*[:#-]?\s*(\d{1,9})\b(?!\s*(?:km|miles?|degrees?|minutes?|hours?)\b)", text)}
    if re.fullmatch(r"\s*\d{1,9}\s*", text):
        ids.add(int(text))
    if re.search(r"\biss\b", text):
        ids.add(25544)
    # Name matching retrieves stable identifiers, never fuzzy invented satellites.
    for row in frame[["norad_id", "name"]].itertuples(index=False):
        name = str(row.name).lower()
        if len(name) >= 4 and name in text:
            ids.add(int(row.norad_id))
    if selected_norad and re.search(r"\b(selected|this) satellite\b", text):
        ids.add(int(selected_norad))
    ids = set(sorted(ids)[:5])
    individual = frame.loc[frame["norad_id"].isin(ids)]
    if ids and not mentioned:
        groups = sorted(set(individual["constellation"].astype(str)))
    subset = frame.loc[frame["constellation"].astype(str).isin(groups)].copy()
    subset["constellation"] = subset["constellation"].astype(str)
    intents = route(text)
    variables = requested_variables(text, frame)
    if len(variables) < 2:
        variables = list(dict.fromkeys(variables + ["altitude_km", "inclination_deg", "period_min", "eccentricity"]))[:4]
    else:
        variables = variables[:4]
    result = {
        "snapshot": snapshot_id(frame), "position_timestamp_utc": str(frame.attrs.get("anchor")),
        "source": "CelesTrak OMM; positions are SGP4 estimates at the starting snapshot, not live observations",
        "analysis_filter": list(selected), "answer_groups": groups,
        "available_groups": sorted(available), "unavailable_groups": sorted(set(groups) - available),
        "group_labels": LABELS, "total_satellites": len(subset),
        "intents": sorted(intents), "missing_norad_ids": sorted(ids - set(individual["norad_id"])),
        "limitations": "No operational status, health, mission history, deployment/deorbit intent, collision predictions, or causal evidence. Element age is relative to position time, not download time. Null means unavailable, never zero. Counts cover the loaded catalogue, not every satellite in existence.",
        "evidence": {},
        "field_definitions": {s.name: {"unit": s.unit, "kind": s.kind.value, "source": s.source,
                                      "meaning": s.note or s.label} for s in COLUMNS},
    }
    evidence = result["evidence"]
    if not individual.empty:
        evidence["satellites"] = records(individual)
    if subset.empty:
        result["limitation"] = "No records for the requested scope. Select or load a group; missing IDs cannot be examined."
        return result
    evidence["population"] = records(stats.composition(subset))
    evidence["group_statistics"] = records(stats.describe(subset, variables))
    # A broad numeric inventory prevents unfamiliar phrasing from hiding available data.
    numeric = [s.name for s in COLUMNS if s.name in subset and s.name != "norad_id"
               and pd.api.types.is_numeric_dtype(subset[s.name])]
    pooled = subset.assign(constellation="answer scope")
    evidence["catalogue_statistics"] = records(stats.describe(pooled, numeric)[
        ["variable", "unit", "n", "missing", "min", "median", "max"]])
    evidence["record_examples"] = {"coverage": "First five NORAD IDs in the answer scope; examples, not a representative sample or a full list.",
                                   "records": records(subset.sort_values("norad_id").head(5))}
    # Safely answer common rankings from actual values, never by executing model code.
    if (requested_variables(text, frame) or re.search(r"\b(fastest|slowest|farthest|closest)\b", text)) and re.search(r"\b(highest|lowest|largest|smallest|most|least|fastest|slowest|farthest|closest)\b", text):
        variable = variables[0]
        ascending = bool(re.search(r"\b(lowest|smallest|least|closest)\b", text))
        if "fastest" in text or "slowest" in text:
            variable = "period_min"
            ascending = "fastest" in text
        ranked = subset.dropna(subset=[variable]).sort_values([variable, "norad_id"], ascending=[ascending, True]).head(5)
        evidence["ranked_records"] = {"ordered_by": variable, "ascending": ascending,
                                      "limit": 5, "records": records(ranked),
                                      "note": "Period ranks orbital frequency, not instantaneous speed; instantaneous speed is not in this frame."}
    # One explicit numerical condition, with units/denominators recorded in the evidence.
    condition = re.search(r"\b(above|over|greater than|more than|below|under|less than|at least|at most)\s+(-?\d[\d,]*(?:\.\d+)?)\s*(km|kilomet(?:er|re)s?|mi|miles?|degrees?|deg|minutes?|min|hours?|h)?\b", text)
    if condition:
        operator, number, unit = condition.groups()
        column = variables[0]
        if not requested_variables(text, frame):
            column = ("inclination_deg" if unit in {"degree", "degrees", "deg"} else
                      "period_min" if unit in {"minute", "minutes", "min"} else
                      "element_age_hours" if unit in {"hour", "hours", "h"} else "altitude_km")
        expected_unit = next(s.unit for s in COLUMNS if s.name == column)
        supplied_unit = ("km" if unit in {"km", "kilometer", "kilometers", "kilometre", "kilometres", "mi", "mile", "miles"} else
                         "deg" if unit in {"degree", "degrees", "deg"} else
                         "min" if unit in {"minute", "minutes", "min"} else
                         "h" if unit in {"hour", "hours", "h"} else expected_unit)
        threshold = float(number.replace(",", ""))
        if unit in {"mi", "mile", "miles"} and column.endswith("_km"):
            threshold *= 1.609344
        values = subset[column]
        compare = {"above": values.gt, "over": values.gt, "greater than": values.gt,
                   "more than": values.gt, "below": values.lt, "under": values.lt,
                   "less than": values.lt, "at least": values.ge, "at most": values.le}[operator]
        matched = subset.loc[compare(threshold) & values.notna()]
        evidence["threshold_query"] = {"column": column, "operator": operator, "threshold": threshold,
                                       "unit": next(s.unit for s in COLUMNS if s.name == column),
                                       "matched": len(matched), "assessed": int(values.notna().sum()),
                                       "missing": int(values.isna().sum()),
                                       "examples": records(matched.head(5)),
                                       "coverage": "This is the parsed single condition; do not claim it answers additional conditions in the question."}
        if supplied_unit != expected_unit:
            evidence["threshold_query"] = {"limitation": "The supplied unit does not match the requested field; ask the user to clarify the field/unit instead of reporting a count."}
    evidence["missing"] = records(stats.missing_report(subset)[["column", "n", "missing", "missing_pct"]])
    ages = subset.loc[subset["element_age_hours"].notna()]
    evidence["freshness"] = records(stats.freshness(ages)) if not ages.empty else []
    result["freshness_warning_hours"] = stats.STALE_ELEMENT_HOURS
    valid = subset.dropna(subset=["altitude_km"])
    if not valid.empty:
        evidence["highest_satellite"] = records(valid.nlargest(1, "altitude_km")[["norad_id", "name", "constellation", "altitude_km"]])
    if intents & {"outliers", "patterns", "summary", "distribution"}:
        summary = overview(subset)
        evidence["altitude_flags"] = {
            "method": METHOD, "unique_flagged": len(summary["flagged"]),
            "assessed": summary["assessed"], "valid_altitudes": summary["valid_altitudes"],
            "strata": records(summary["outlier_summary"]),
        }
        if ids:
            flags = summary["flagged"]
            evidence["individual_flags"] = {
                "requested_ids": sorted(ids),
                "flagged_records": records(flags.loc[flags["norad_id"].isin(ids)]) if not flags.empty else [],
                "note": "An absent flag is not proof of normality: check missing altitude and strata with fewer than four valid values.",
            }
        # Explicit fixed-width bins show populations without claiming orbital shells or causes.
        bins = valid.assign(lower_km=np.floor(valid["altitude_km"] / 25) * 25)
        counts = bins.groupby(["constellation", "lower_km"], observed=True).size().rename("n").reset_index()
        top = counts.sort_values("n", ascending=False).groupby("constellation", sort=False).head(6)
        evidence["altitude_bins"] = {"width_km": 25, "coverage": "Six most populated bins per group; not the full distribution", "bins": records(top)}
    if intents & {"correlations", "patterns"}:
        pairs = stats.pooled_vs_within(subset, variables)
        # Existing pooled_n is catalogue size; report the actual complete-pair count here.
        for index, row in pairs.iterrows():
            pairs.at[index, "pooled_n"] = len(subset[[row.x, row.y]].dropna())
        evidence["correlations"] = records(pairs)
        result["correlation_method"] = "Spearman rank coefficients; pairwise complete observations; within-group minimum n=10. Undefined is not zero. Correlation is not causation."
    return fit_context(result)
