"""Phase 12: read-only AI Assistant tool layer.

Every function here wraps an ALREADY-EXISTING CRISIS-X endpoint function
(app/api/*.py) exactly as written -- no hazard/exposure/risk/routing/
scenario computation logic is reimplemented in this module. Calling the
existing route handler functions directly (not via HTTP) is a
deliberate choice: every non-trivial read here already has real
orchestration logic (CRS-aware extent computation, effective-layer
resolution, scenario comparison, etc.) living only in app/api/*.py --
those functions are plain, directly-callable Python under their
`@router` decorator, so importing and calling them verbatim is what
"reuse existing services/functions/endpoints, do not duplicate their
business logic" means in practice for retrieval that has no separate
service-layer function to call instead.

Every tool:
- takes a real SQLAlchemy Session -- this module is the only place in
  the assistant package that touches the database, and only through
  calls to existing, already-tested endpoint functions.
- NEVER raises out to its caller. Any failure (not found, wrong type,
  wrong status, unexpected error) becomes a returned AssistantEvidence
  with status="unavailable" or "error" and a human-readable `detail` --
  the orchestrator/provider must never see a stack trace as if it were
  data.
- returns `limitations`/`provenance` copied VERBATIM from the
  underlying Dataset/analysis result, never reconstructed or
  paraphrased.
- is strictly read-only: no tool here creates, updates, or deletes
  anything. Phase 12 defines no action/compute tools.
"""

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.datasets import get_dataset as _get_dataset_endpoint
from app.api.datasets import get_dataset_geojson as _get_dataset_geojson_endpoint
from app.api.datasets import list_datasets as _list_datasets_endpoint
from app.api.digital_twin import get_digital_twin_state as _get_digital_twin_state_endpoint
from app.api.exposure import get_exposure_analysis as _get_exposure_analysis_endpoint
from app.api.exposure import list_exposure_analyses as _list_exposure_analyses_endpoint
from app.api.hazards import get_hazard_scenario as _get_hazard_scenario_endpoint
from app.api.hazards import list_hazard_scenario_datasets as _list_hazard_scenario_datasets_endpoint
from app.api.hazards import list_hazard_scenarios as _list_hazard_scenarios_endpoint
from app.api.risk import get_risk_analysis as _get_risk_analysis_endpoint
from app.api.risk import list_risk_analyses as _list_risk_analyses_endpoint
from app.api.routing import get_route_analysis as _get_route_analysis_endpoint
from app.api.routing import list_route_analyses as _list_route_analyses_endpoint
from app.api.scenarios import create_scenario_comparison as _create_scenario_comparison_endpoint
from app.api.scenarios import get_scenario_state as _get_scenario_state_endpoint
from app.schemas.assistant import AssistantEvidence
from app.schemas.dataset import DatasetRead
from app.schemas.digital_twin import DigitalTwinStateRead
from app.schemas.exposure import ExposureAnalysisRead
from app.schemas.hazard import HazardScenarioRead
from app.schemas.risk import RiskAnalysisRead
from app.schemas.routing import RouteAnalysisRead
from app.schemas.scenario import ScenarioComparisonRequest, ScenarioStateRead
from app.services.preview import DEFAULT_COORDINATE_PRECISION

__all__ = [
    "get_twin_state",
    "get_scenario_state",
    "list_hazard_scenarios",
    "get_hazard_scenario",
    "list_exposure_analyses",
    "get_exposure_analysis",
    "list_risk_analyses",
    "get_risk_analysis",
    "find_highest_risk_classes",
    "list_route_analyses",
    "get_route_analysis",
    "get_dataset",
    "get_dataset_geojson",
    "list_project_datasets",
    "compare_scenario_analyses",
]

# Far below the underlying endpoints' own defaults (20,000 features /
# unbounded list) -- keeps evidence payloads sized for an LLM's context
# window. A real truncation is always surfaced (`truncated`/
# `total_count`), never silently dropped, mirroring the same discipline
# app/services/preview.py's own GeoJSON envelope already uses.
DEFAULT_ASSISTANT_LIST_LIMIT = 20
DEFAULT_ASSISTANT_GEOJSON_LIMIT = 200

_HIGHEST_KEYWORDS = ("highest", "most", "worst", "top")


def _unavailable(tool: str, detail: str) -> AssistantEvidence:
    return AssistantEvidence(tool=tool, status="unavailable", data=None, limitations=[], provenance=None, detail=detail)


def _error(tool: str, detail: str) -> AssistantEvidence:
    return AssistantEvidence(tool=tool, status="error", data=None, limitations=[], provenance=None, detail=detail)


def _safe(tool: str, build: Callable[[], AssistantEvidence]) -> AssistantEvidence:
    """Shared boundary: HTTPException (404/400/410/422 -- the existing
    fail-closed pattern every endpoint already uses) always becomes
    `unavailable` with the endpoint's own detail message verbatim; any
    other exception becomes `error`. A tool never lets an exception
    escape to the orchestrator.
    """
    try:
        return build()
    except HTTPException as exc:
        return _unavailable(tool, str(exc.detail))
    except Exception as exc:  # noqa: BLE001 -- tool boundary must never raise
        return _error(tool, str(exc))


def _paginate(items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    total = len(items)
    return {"items": items[:limit], "total_count": total, "truncated": total > limit}


# --- Digital Twin -----------------------------------------------------------------


def get_twin_state(db: Session, twin_id: str) -> AssistantEvidence:
    """Full composed Digital Twin state: layers by category, spatial
    extent, acquisition date range, missing recommended layers, and
    standing limitations -- wraps GET /digital-twins/{twin_id}/state
    (app/api/digital_twin.py::get_digital_twin_state) verbatim.
    """

    def _build() -> AssistantEvidence:
        result: DigitalTwinStateRead = _get_digital_twin_state_endpoint(twin_id, db)
        data = result.model_dump(mode="json")
        return AssistantEvidence(
            tool="get_twin_state",
            status="success",
            data=data,
            limitations=list(data.get("limitations") or []),
            provenance=None,
        )

    return _safe("get_twin_state", _build)


# --- Scenario Lab ------------------------------------------------------------------


def get_scenario_state(db: Session, scenario_id: str) -> AssistantEvidence:
    """Effective layers (override-wins-over-baseline) plus every
    hazard/exposure/risk/route analysis run against this scenario --
    wraps GET /scenarios/{scenario_id}/state
    (app/api/scenarios.py::get_scenario_state) verbatim. This is the
    single richest read for "what does this scenario currently show and
    what has been computed against it."
    """

    def _build() -> AssistantEvidence:
        result: ScenarioStateRead = _get_scenario_state_endpoint(scenario_id, db)
        data = result.model_dump(mode="json")
        return AssistantEvidence(tool="get_scenario_state", status="success", data=data, limitations=[], provenance=None)

    return _safe("get_scenario_state", _build)


def compare_scenario_analyses(
    db: Session, analysis_type: str, left_analysis_id: str, right_analysis_id: str
) -> AssistantEvidence:
    """Compares two already-completed analyses of the same type (exposure/
    risk/route) using the existing Scenario Lab comparison functionality
    -- wraps POST /scenario-comparisons
    (app/api/scenarios.py::create_scenario_comparison ->
    app/services/scenario.py::compute_comparison) verbatim. Computes no
    new diff logic here.
    """

    def _build() -> AssistantEvidence:
        payload = ScenarioComparisonRequest(
            analysis_type=analysis_type,
            left_analysis_id=left_analysis_id,
            right_analysis_id=right_analysis_id,
        )
        result = _create_scenario_comparison_endpoint(payload, db)
        data = result.model_dump(mode="json")
        # comparability_warnings is this endpoint's own disclosure field
        # (e.g. the two analyses used different hazard types) -- treated
        # the same as `limitations` elsewhere: surfaced verbatim, never
        # dropped.
        return AssistantEvidence(
            tool="compare_scenario_analyses",
            status="success",
            data=data,
            limitations=list(data.get("comparability_warnings") or []),
            provenance=None,
        )

    return _safe("compare_scenario_analyses", _build)


# --- Hazard ------------------------------------------------------------------------


def list_hazard_scenarios(
    db: Session, project_id: str, hazard_type: str | None = None, limit: int = DEFAULT_ASSISTANT_LIST_LIMIT
) -> AssistantEvidence:
    def _build() -> AssistantEvidence:
        scenarios = _list_hazard_scenarios_endpoint(project_id, hazard_type, db)
        items = [HazardScenarioRead.model_validate(s).model_dump(mode="json") for s in scenarios]
        return AssistantEvidence(
            tool="list_hazard_scenarios", status="success", data=_paginate(items, limit), limitations=[], provenance=None
        )

    return _safe("list_hazard_scenarios", _build)


def get_hazard_scenario(db: Session, scenario_id: str) -> AssistantEvidence:
    """A single hazard scenario's parameters/status plus its output
    dataset(s) -- a HazardScenario row itself carries no `limitations`
    field (see app/api/hazards.py); that disclosure text lives on the
    output Dataset's `provenance`/`metadata_json` instead, so both are
    fetched and returned together.
    """

    def _build() -> AssistantEvidence:
        scenario = _get_hazard_scenario_endpoint(scenario_id, db)
        datasets = _list_hazard_scenario_datasets_endpoint(scenario_id, db)
        scenario_data = HazardScenarioRead.model_validate(scenario).model_dump(mode="json")
        dataset_data = [DatasetRead.model_validate(d).model_dump(mode="json") for d in datasets]
        limitations: list[str] = []
        for dataset in dataset_data:
            limitations.extend((dataset.get("provenance") or {}).get("limitations") or [])
        return AssistantEvidence(
            tool="get_hazard_scenario",
            status="success",
            data={"scenario": scenario_data, "datasets": dataset_data},
            limitations=limitations,
            provenance=None,
        )

    return _safe("get_hazard_scenario", _build)


# --- Exposure ------------------------------------------------------------------------


def list_exposure_analyses(
    db: Session, project_id: str, exposure_dataset_type: str | None = None, limit: int = DEFAULT_ASSISTANT_LIST_LIMIT
) -> AssistantEvidence:
    def _build() -> AssistantEvidence:
        analyses = _list_exposure_analyses_endpoint(project_id, exposure_dataset_type, db)
        items = [ExposureAnalysisRead.model_validate(a).model_dump(mode="json") for a in analyses]
        return AssistantEvidence(
            tool="list_exposure_analyses",
            status="success",
            data=_paginate(items, limit),
            limitations=[],
            provenance=None,
        )

    return _safe("list_exposure_analyses", _build)


def get_exposure_analysis(db: Session, analysis_id: str) -> AssistantEvidence:
    def _build() -> AssistantEvidence:
        analysis = _get_exposure_analysis_endpoint(analysis_id, db)
        data = ExposureAnalysisRead.model_validate(analysis).model_dump(mode="json")
        limitations = list((data.get("results") or {}).get("limitations") or [])
        return AssistantEvidence(
            tool="get_exposure_analysis", status="success", data=data, limitations=limitations, provenance=None
        )

    return _safe("get_exposure_analysis", _build)


# --- Risk --------------------------------------------------------------------------


def list_risk_analyses(
    db: Session,
    project_id: str,
    hazard_dataset_type: str | None = None,
    exposure_dataset_type: str | None = None,
    limit: int = DEFAULT_ASSISTANT_LIST_LIMIT,
) -> AssistantEvidence:
    def _build() -> AssistantEvidence:
        analyses = _list_risk_analyses_endpoint(project_id, hazard_dataset_type, exposure_dataset_type, db)
        items = [RiskAnalysisRead.model_validate(a).model_dump(mode="json") for a in analyses]
        return AssistantEvidence(
            tool="list_risk_analyses", status="success", data=_paginate(items, limit), limitations=[], provenance=None
        )

    return _safe("list_risk_analyses", _build)


def get_risk_analysis(db: Session, analysis_id: str) -> AssistantEvidence:
    def _build() -> AssistantEvidence:
        analysis = _get_risk_analysis_endpoint(analysis_id, db)
        data = RiskAnalysisRead.model_validate(analysis).model_dump(mode="json")
        limitations = list((data.get("results") or {}).get("limitations") or [])
        return AssistantEvidence(tool="get_risk_analysis", status="success", data=data, limitations=limitations, provenance=None)

    return _safe("get_risk_analysis", _build)


def find_highest_risk_classes(db: Session, risk_analysis_id: str, top_n: int = 5) -> AssistantEvidence:
    """Sorts an already-computed risk analysis's `by_class` entries by
    `risk_score` descending -- pure re-ordering of existing output,
    computes no new score. Per RISK_LIMITATIONS (surfaced below
    verbatim), risk_score must never be read as an aggregate/city-wide
    figure -- only compared class-to-class within this SAME analysis.
    """

    def _build() -> AssistantEvidence:
        analysis = _get_risk_analysis_endpoint(risk_analysis_id, db)
        data = RiskAnalysisRead.model_validate(analysis).model_dump(mode="json")
        results = data.get("results") or {}
        by_class = results.get("by_class") or {}
        ranked = sorted(
            ({"hazard_class_label": label, **entry} for label, entry in by_class.items()),
            key=lambda entry: entry.get("risk_score", float("-inf")),
            reverse=True,
        )[: max(0, top_n)]
        return AssistantEvidence(
            tool="find_highest_risk_classes",
            status="success",
            data={"risk_analysis_id": risk_analysis_id, "ranked_classes": ranked},
            limitations=list(results.get("limitations") or []),
            provenance=None,
        )

    return _safe("find_highest_risk_classes", _build)


# --- Routing -------------------------------------------------------------------------


def list_route_analyses(
    db: Session, project_id: str, hazard_dataset_type: str | None = None, limit: int = DEFAULT_ASSISTANT_LIST_LIMIT
) -> AssistantEvidence:
    def _build() -> AssistantEvidence:
        analyses = _list_route_analyses_endpoint(project_id, hazard_dataset_type, db)
        items = [RouteAnalysisRead.model_validate(a).model_dump(mode="json") for a in analyses]
        return AssistantEvidence(
            tool="list_route_analyses", status="success", data=_paginate(items, limit), limitations=[], provenance=None
        )

    return _safe("list_route_analyses", _build)


def get_route_analysis(db: Session, analysis_id: str) -> AssistantEvidence:
    def _build() -> AssistantEvidence:
        analysis = _get_route_analysis_endpoint(analysis_id, db)
        data = RouteAnalysisRead.model_validate(analysis).model_dump(mode="json")
        limitations = list((data.get("results") or {}).get("limitations") or [])
        return AssistantEvidence(
            tool="get_route_analysis", status="success", data=data, limitations=limitations, provenance=None
        )

    return _safe("get_route_analysis", _build)


# --- Dataset ------------------------------------------------------------------------


def get_dataset(db: Session, dataset_id: str) -> AssistantEvidence:
    def _build() -> AssistantEvidence:
        dataset = _get_dataset_endpoint(dataset_id, db)
        data = DatasetRead.model_validate(dataset).model_dump(mode="json")
        provenance = data.get("provenance")
        limitations = list((provenance or {}).get("limitations") or [])
        return AssistantEvidence(
            tool="get_dataset", status="success", data=data, limitations=limitations, provenance=provenance
        )

    return _safe("get_dataset", _build)


def get_dataset_geojson(db: Session, dataset_id: str, limit: int = DEFAULT_ASSISTANT_GEOJSON_LIMIT) -> AssistantEvidence:
    """WGS84 GeoJSON features for a vector dataset -- wraps
    GET /datasets/{dataset_id}/geojson
    (app/api/datasets.py::get_dataset_geojson ->
    app/services/preview.py::build_geojson_preview) verbatim, only with
    a much smaller default `limit` than that endpoint's own 20,000, to
    keep the payload LLM-context-sized. `truncated`/`total_feature_count`
    are the endpoint's own envelope fields, never recomputed here.
    """

    def _build() -> AssistantEvidence:
        data = _get_dataset_geojson_endpoint(dataset_id, limit, DEFAULT_COORDINATE_PRECISION, db)
        return AssistantEvidence(tool="get_dataset_geojson", status="success", data=data, limitations=[], provenance=None)

    return _safe("get_dataset_geojson", _build)


def list_project_datasets(
    db: Session, project_id: str, dataset_type: str | None = None, limit: int = DEFAULT_ASSISTANT_LIST_LIMIT
) -> AssistantEvidence:
    """Lists a project's datasets (name/type/status/provenance) --
    wraps GET /projects/{project_id}/datasets
    (app/api/datasets.py::list_datasets) verbatim.
    """

    def _build() -> AssistantEvidence:
        datasets = _list_datasets_endpoint(project_id, dataset_type, db)
        items = [DatasetRead.model_validate(d).model_dump(mode="json") for d in datasets]
        return AssistantEvidence(
            tool="list_project_datasets", status="success", data=_paginate(items, limit), limitations=[], provenance=None
        )

    return _safe("list_project_datasets", _build)
