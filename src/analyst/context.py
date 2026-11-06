"""Deterministic retrieval over the same snapshot used by Analysis and Globe."""
from __future__ import annotations

import hashlib
import json
import re

import numpy as np
import pandas as pd

from src.analysis import stats
from src.analysis.overview import overview
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
}
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
    groups = mentioned or list(selected)
    if re.search(r"\b(all|entire|whole) (loaded |available )?(groups|dataset|catalogue|catalog|satellites)\b", text):
        groups = sorted(available)
    ids = {int(n) for n in re.findall(r"\b\d{5,9}\b", text)}
    if "iss" in text.split():
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
    variables = [key for key, aliases in VARIABLES.items() if key in frame and
                 (key in text or any(alias in text for alias in aliases))]
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
    }
    evidence = result["evidence"]
    if not individual.empty:
        evidence["satellites"] = records(individual[[c for c in (
            "norad_id", "name", "constellation", "regime", "epoch", "element_age_hours",
            "altitude_km", "inclination_deg", "eccentricity", "period_min", "latitude_deg", "longitude_deg"
        ) if c in individual]])
    if subset.empty:
        result["limitation"] = "No records for the requested scope. Select or load a group; missing IDs cannot be examined."
        return result
    evidence["population"] = records(stats.composition(subset))
    evidence["group_statistics"] = records(stats.describe(subset, variables))
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
    # Trim optional details in whole records, never cut JSON or remove denominator/method.
    for key in ("altitude_bins", "correlations"):
        if len(json.dumps(result, ensure_ascii=True)) > MAX_CONTEXT:
            evidence.pop(key, None)
            result.setdefault("omitted_evidence", []).append(key)
    if len(json.dumps(result, ensure_ascii=True)) > MAX_CONTEXT:
        raise ValueError("This context is too large. Ask about fewer groups or one satellite.")
    return result
