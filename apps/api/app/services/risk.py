"""Phase 7: Risk Engine -- combines a completed Phase 6 exposure analysis
with two explicit, user-declared assumptions (vulnerability_weight,
consequence_weight) into a per-hazard-class risk score.

Hazard != Exposure != Risk: this module never re-derives hazard
classification or re-runs the spatial overlay -- both already happened in
Phase 4/5 (hazard) and Phase 6 (exposure). It only adds a risk_score/
risk_class to each hazard class already present in the exposure analysis's
`by_class` breakdown, and -- if a per-feature layer is supplied -- joins
that same risk_score/risk_class onto it by matching hazard_class_code.

risk_score = hazard_intensity_weight x vulnerability_weight x consequence_weight

- hazard_intensity_weight is derived deterministically from the hazard
  class code, anchored to Phase 4/5's fixed classification scheme (not the
  classes present in any one analysis) -- never user-configurable.
- vulnerability_weight and consequence_weight are explicit, required,
  caller-declared assumptions in [0, 1] -- never defaulted or inferred.

IMPORTANT -- what risk_score is NOT: it is a per-hazard-class
INTENSITY/SCREENING score conditional on an asset being exposed to that
class. It is NOT weighted, scaled, or normalized by exposure quantity
(population count, building count, road length, area, ...) -- inventing
such a normalization would be an arbitrary, unjustified design choice, not
a physical or statistical necessity. Exposure quantity is preserved
unchanged, side by side with risk_score, in every `by_class` entry.
risk_score must never be read, aggregated, or reported as a total or
project-level risk magnitude.

See docs/architecture/0008-phase-7-risk-engine.md for the full reasoning.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np

# Fixed per hazard type, anchored to Phase 4/5's fixed classification
# scheme -- NOT max(codes present in a particular analysis). A small AOI
# that only exhibits landslide classes 1-2 must not have class 2 suddenly
# treated as "maximum severity" just because it's the highest code present.
HAZARD_CLASS_MAX_CODE = {
    "flood_inundation": 1,  # exposure's classify_hazard_array: single class, code 1
    "eo_change_mask": 1,  # codes 0 (no_change) / 1 (changed)
    "landslide_susceptibility": 5,  # hazard_landslide.CLASS_LEGEND is always 5 ordinal classes
}

RISK_CLASS_LEGEND = {
    "1": "very_low",
    "2": "low",
    "3": "moderate",
    "4": "high",
    "5": "very_high",
}

DEFAULT_RISK_BREAKPOINTS: tuple[float, float, float, float] = (0.2, 0.4, 0.6, 0.8)

RISK_METHOD = "hazard_vulnerability_consequence_product"

RISK_LIMITATIONS = [
    "This is a spatial/class-based risk INTENSITY screening (hazard x vulnerability x consequence), "
    "conditional on an asset being exposed to a given hazard class -- it is not a deterministic "
    "prediction of loss, damage, or disaster occurrence.",
    "risk_score is NOT weighted, scaled, or normalized by exposure quantity (count, length_m, area_m2, "
    "population_sum, ...). It must never be read as a total, aggregate, or project-level risk "
    "magnitude -- exposure quantity is reported separately, unchanged, in the same by_class entry.",
    "vulnerability_weight and consequence_weight are explicit user-declared assumptions for this "
    "analysis, not measured or modeled properties of the exposed assets.",
    "No monetary value, casualty count, or structural damage is estimated by this analysis.",
]


def compute_hazard_intensity_weight(hazard_dataset_type: str, hazard_class_code: int) -> float:
    """hazard_class_code / the fixed max class code for this hazard type
    (see HAZARD_CLASS_MAX_CODE) -- deterministic, not user-configurable.
    Raises ValueError for an unsupported hazard_dataset_type -- fails
    closed rather than guessing at an unfamiliar classification scheme.
    """
    if hazard_dataset_type not in HAZARD_CLASS_MAX_CODE:
        raise ValueError(
            f"Unsupported hazard_dataset_type '{hazard_dataset_type}' for risk scoring. "
            f"Supported: {sorted(HAZARD_CLASS_MAX_CODE)}."
        )
    max_code = HAZARD_CLASS_MAX_CODE[hazard_dataset_type]
    return hazard_class_code / max_code


def compute_risk_score(
    hazard_intensity_weight: float, vulnerability_weight: float, consequence_weight: float
) -> float:
    """The product of three already-[0,1]-bounded factors -- see module
    docstring for why exposure quantity is deliberately not a fourth
    factor here.
    """
    return hazard_intensity_weight * vulnerability_weight * consequence_weight


def classify_risk_score(
    risk_score: float, breakpoints: tuple[float, float, float, float] = DEFAULT_RISK_BREAKPOINTS
) -> str:
    """Classifies a risk_score (expected in [0, 1]) into 5 ordinal risk
    classes via 4 ascending breakpoints. Boundary convention: a value
    exactly at a breakpoint falls into the *higher* class -- left-inclusive
    intervals, same convention as hazard_landslide.classify_slope.
    """
    if len(breakpoints) != 4:
        raise ValueError("breakpoints must contain exactly 4 values")
    if list(breakpoints) != sorted(breakpoints) or len(set(breakpoints)) != 4:
        raise ValueError("breakpoints must be strictly ascending")

    code = int(np.digitize([risk_score], breakpoints)[0]) + 1  # 1..5
    return RISK_CLASS_LEGEND[str(code)]


def build_risk_by_class(
    by_class: dict[str, dict[str, Any]],
    hazard_dataset_type: str,
    *,
    vulnerability_weight: float,
    consequence_weight: float,
    risk_breakpoints: tuple[float, float, float, float] = DEFAULT_RISK_BREAKPOINTS,
) -> dict[str, dict[str, Any]]:
    """Adds hazard_intensity_weight/risk_score/risk_class to each entry of
    an exposure analysis's `by_class` breakdown. Every pre-existing
    exposure-quantity key (count/sum/length_m/feature_count/area_m2/
    population_sum) is carried through completely unchanged -- risk_score
    is never used to scale, weight, or replace those figures. Empty input
    -> empty output, a valid completion (matches Phase 6's own "no overlap"
    case), not an error.
    """
    result: dict[str, dict[str, Any]] = {}
    for label, entry in by_class.items():
        hazard_class_code = int(entry["hazard_class_code"])
        hazard_intensity_weight = compute_hazard_intensity_weight(hazard_dataset_type, hazard_class_code)
        risk_score = compute_risk_score(hazard_intensity_weight, vulnerability_weight, consequence_weight)
        risk_class = classify_risk_score(risk_score, risk_breakpoints)
        result[label] = {
            **entry,
            "hazard_intensity_weight": hazard_intensity_weight,
            "risk_score": risk_score,
            "risk_class": risk_class,
        }
    return result


def attach_risk_to_features(features_gdf: Any, risk_by_class: dict[str, dict[str, Any]]) -> Any:
    """Joins risk_score/risk_class onto a per-feature GeoDataFrame (Phase
    6's exposure_features output, which always carries a hazard_class_code
    column regardless of asset geometry type) by matching hazard_class_code.
    Row count, geometry, and every existing column are left unchanged; only
    risk_score/risk_class are added.
    """
    score_by_code: dict[int, float] = {}
    class_by_code: dict[int, str] = {}
    for entry in risk_by_class.values():
        code = int(entry["hazard_class_code"])
        score_by_code[code] = entry["risk_score"]
        class_by_code[code] = entry["risk_class"]

    result = features_gdf.copy()
    result["risk_score"] = result["hazard_class_code"].map(score_by_code)
    result["risk_class"] = result["hazard_class_code"].map(class_by_code)
    return result


@dataclass
class RiskAnalysisComputation:
    results: dict[str, Any]
    output_gdf: Any | None


def run_risk_analysis(
    exposure_results: dict[str, Any],
    hazard_dataset_type: str,
    *,
    vulnerability_weight: float,
    consequence_weight: float,
    risk_breakpoints: tuple[float, float, float, float] = DEFAULT_RISK_BREAKPOINTS,
    features_gdf: Any | None = None,
) -> RiskAnalysisComputation:
    """Pure computation entry point: builds the risk `by_class` breakdown
    from an already-computed Phase 6 `results` dict, and -- if a per-feature
    GeoDataFrame is supplied -- joins risk_score/risk_class onto it. Does
    not touch disk; the API layer handles reading exposure_features and
    writing the output GeoJSON, matching Phase 6's separation of pure
    computation from raster/vector I/O.
    """
    by_class = exposure_results.get("by_class", {})
    risk_by_class = build_risk_by_class(
        by_class,
        hazard_dataset_type,
        vulnerability_weight=vulnerability_weight,
        consequence_weight=consequence_weight,
        risk_breakpoints=risk_breakpoints,
    )

    limitations = list(RISK_LIMITATIONS) + list(exposure_results.get("limitations", []))

    results: dict[str, Any] = {
        "method": RISK_METHOD,
        "hazard_dataset_type": hazard_dataset_type,
        "vulnerability_weight": vulnerability_weight,
        "consequence_weight": consequence_weight,
        "risk_breakpoints": list(risk_breakpoints),
        "risk_class_legend": RISK_CLASS_LEGEND,
        "by_class": risk_by_class,
        "limitations": limitations,
    }

    output_gdf = None
    if features_gdf is not None and not features_gdf.empty and risk_by_class:
        output_gdf = attach_risk_to_features(features_gdf, risk_by_class)

    return RiskAnalysisComputation(results=results, output_gdf=output_gdf)
