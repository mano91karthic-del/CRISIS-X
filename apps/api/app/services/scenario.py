"""Phase 10: Scenario Lab -- pure composition/aggregation functions for
building controlled what-if states on top of a Digital Twin's baseline.

Scenario Lab does not duplicate any Phase 2-8 science: hazard/exposure/
risk/route "runs" triggered through it call the exact same
`run_flood_scenario`/`run_landslide_scenario`/`run_exposure_analysis`/
`run_risk_analysis`/`run_route_analysis` functions those phases already
expose (see app/api/scenarios.py). What lives here is composition only:
resolving which layer a scenario currently believes for each dataset_type
(baseline vs. override), and diffing two already-computed analysis
`results` dicts -- aggregation, not modeling.

See docs/architecture/0011-phase-10-scenario-lab.md for the full reasoning.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class LayerRef:
    dataset_type: str
    category: str
    dataset_id: str | None
    source: str  # "baseline" | "override"


def resolve_effective_layers(baseline: list[LayerRef], overrides: list[LayerRef]) -> dict[str, LayerRef]:
    """What a scenario currently assumes for each dataset_type: an override
    always wins over the frozen baseline for the same dataset_type: never
    merged, never averaged. A dataset_type present in neither list is
    simply absent from the result -- never fabricated.
    """
    effective: dict[str, LayerRef] = {}
    for layer in baseline:
        effective[layer.dataset_type] = layer
    for layer in overrides:
        effective[layer.dataset_type] = layer
    return effective


def can_mutate_overrides(dataset_type: str, consumed_dataset_types: set[str]) -> bool:
    """A scenario's override for a given dataset_type may be added/removed
    only until some run in this scenario has actually resolved and
    consumed that exact dataset_type as an input. Scoped per dataset_type,
    not scenario-wide: every scenario-triggered analysis record is
    self-contained (it stores its own resolved dataset ids in its own
    `parameters`/`*_dataset_id` columns at creation time, exactly like
    every Phase 4-8 analysis already does), so a hazard run that only
    ever consumes "dem" creates no staleness risk for a later override of,
    say, "landslide_susceptibility" -- freezing every dataset_type the
    moment ANY run exists would block the plan's own core workflow (run an
    alternate hazard -> register it as an override -> run exposure/risk/
    route against it). Freezing only the dataset_type(s) actually consumed
    preserves the "no silent drift under an already-issued run" intent
    without blocking that workflow.
    """
    return dataset_type not in consumed_dataset_types


# --- comparison / diff -------------------------------------------------------


_NUMERIC_STAT_KEYS = (
    "count",
    "sum",
    "length_m",
    "feature_count",
    "area_m2",
    "population_sum",
    "hazard_intensity_weight",
    "risk_score",
)


def _numeric_delta(left: float | None, right: float | None) -> dict[str, Any]:
    left_value = left or 0.0
    right_value = right or 0.0
    delta = right_value - left_value
    pct_change = (delta / left_value) if left_value else None
    return {"left": left_value, "right": right_value, "delta": delta, "pct_change": pct_change}


def diff_by_class(left_by_class: dict[str, Any], right_by_class: dict[str, Any]) -> dict[str, Any]:
    """Per-hazard-class diff for exposure/risk `results["by_class"]` dicts,
    keyed by the same class labels those results already use. A class
    present on only one side is treated as zero (absent, not exposed) on
    the other -- never dropped from the diff silently. `risk_class` (a
    string, not numeric) is reported as old/right values plus a `changed`
    flag rather than a numeric delta.
    """
    labels = sorted(set(left_by_class) | set(right_by_class))
    result: dict[str, Any] = {}
    for label in labels:
        left_entry = left_by_class.get(label, {})
        right_entry = right_by_class.get(label, {})
        entry: dict[str, Any] = {}
        for key in _NUMERIC_STAT_KEYS:
            if key in left_entry or key in right_entry:
                entry[key] = _numeric_delta(left_entry.get(key), right_entry.get(key))
        if "risk_class" in left_entry or "risk_class" in right_entry:
            left_class = left_entry.get("risk_class")
            right_class = right_entry.get("risk_class")
            entry["risk_class"] = {"left": left_class, "right": right_class, "changed": left_class != right_class}
        result[label] = entry
    return result


def diff_route_analysis(left_results: dict[str, Any], right_results: dict[str, Any]) -> dict[str, Any]:
    """Diff for RouteAnalysis `results` dicts: both named routes' distance/
    hazard/cost metrics, plus the graph-level blocked-edge summary. An
    infeasible route's missing metrics are treated as zero via
    `_numeric_delta`'s None handling -- `feasible` is reported separately
    so that substitution is never mistaken for "no hazard cost."
    """
    diff: dict[str, Any] = {}
    for route_key in ("shortest_route", "hazard_aware_route"):
        left_route = left_results.get(route_key, {})
        right_route = right_results.get(route_key, {})
        diff[route_key] = {
            "feasible": {"left": left_route.get("feasible"), "right": right_route.get("feasible")},
            "distance_m": _numeric_delta(left_route.get("distance_m"), right_route.get("distance_m")),
            "hazard_component_m": _numeric_delta(
                left_route.get("hazard_component_m"), right_route.get("hazard_component_m")
            ),
            "cost_m_equivalent": _numeric_delta(
                left_route.get("cost_m_equivalent"), right_route.get("cost_m_equivalent")
            ),
        }
    left_graph = left_results.get("graph_summary", {})
    right_graph = right_results.get("graph_summary", {})
    diff["graph_summary"] = {
        "blocked_edge_count": _numeric_delta(left_graph.get("blocked_edge_count"), right_graph.get("blocked_edge_count")),
        "total_blocked_length_m": _numeric_delta(
            left_graph.get("total_blocked_length_m"), right_graph.get("total_blocked_length_m")
        ),
    }
    return diff


@dataclass
class ComparisonResult:
    diff: dict[str, Any]
    comparability_warnings: list[str]


_SUPPORTED_COMPARISON_TYPES = {"exposure", "risk", "route"}


def compute_comparison(
    analysis_type: str,
    left_results: dict[str, Any],
    right_results: dict[str, Any],
    left_hazard_dataset_type: str | None,
    right_hazard_dataset_type: str | None,
) -> ComparisonResult:
    """Pure aggregation over two already-computed `results` dicts -- no new
    modeling. Raises ValueError for an unsupported analysis_type, fails
    closed rather than guessing at an unfamiliar results shape.
    """
    if analysis_type not in _SUPPORTED_COMPARISON_TYPES:
        raise ValueError(
            f"Unsupported analysis_type '{analysis_type}' for comparison. "
            f"Supported: {sorted(_SUPPORTED_COMPARISON_TYPES)}."
        )

    warnings: list[str] = []
    if left_hazard_dataset_type != right_hazard_dataset_type:
        warnings.append(
            f"hazard_dataset_type differs between the two analyses ('{left_hazard_dataset_type}' vs "
            f"'{right_hazard_dataset_type}'); class-by-class comparison may not be meaningful."
        )

    if analysis_type in ("exposure", "risk"):
        diff = diff_by_class(left_results.get("by_class", {}), right_results.get("by_class", {}))
    else:
        diff = diff_route_analysis(left_results, right_results)
        left_feasible = left_results.get("shortest_route", {}).get("feasible")
        right_feasible = right_results.get("shortest_route", {}).get("feasible")
        if left_feasible != right_feasible:
            warnings.append(
                "Route feasibility differs between the two analyses; an infeasible route's missing "
                "distance/hazard/cost metrics are treated as zero in the diff above, not as 'no hazard'."
            )

    return ComparisonResult(diff=diff, comparability_warnings=warnings)
