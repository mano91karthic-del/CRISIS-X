"""Phase 12: assistant orchestrator.

The only entry point that ties together: dashboard context -> read-only
tool calls -> evidence collection -> AI provider -> final answer.

Strictly forbidden here, by construction: no raw SQL, no direct ORM
mutation, no calls to any hazard/exposure/risk/routing/scenario COMPUTE
function, no dataset upload/delete, no twin-layer registration, no
scenario mutation. Every read this module performs goes through
app.services.assistant.tools, which is itself read-only and is the only
place in this package that touches a Session at all.

Tool selection is a small, deterministic keyword heuristic over the
question text plus whatever context the dashboard supplied (twin_id/
scenario_id/active_feature) -- not a second LLM call. This keeps the
orchestrator's behavior fully inspectable and testable without a real
provider (see tests/test_assistant_orchestrator.py), while still
satisfying "determine which read-only tools are relevant" -- a future
phase could replace this heuristic with provider-driven tool selection
without changing this module's public contract (`answer_question`).
"""

from sqlalchemy.orm import Session

from app.schemas.assistant import AssistantEvidence, AssistantQueryRequest, AssistantQueryResponse
from app.services.assistant import tools
from app.services.assistant.provider import AIProvider

# Bounds how many tool calls a single question can trigger -- keeps the
# orchestrator's worst case cheap and deterministic, and gives tests a
# concrete number to assert against.
MAX_TOOL_CALLS = 8

_HAZARD_KEYWORDS = ("hazard", "flood", "landslide", "susceptib", "inundat")
_EXPOSURE_KEYWORDS = ("exposure", "exposed", "building", "population", "asset")
_RISK_KEYWORDS = ("risk",)
_ROUTE_KEYWORDS = ("route", "evacuat", "road", "path")
_DATASET_KEYWORDS = ("dataset", "data set", "layer", "layers")
_HIGHEST_KEYWORDS = ("highest", "most", "worst", "top")
_TWIN_KEYWORDS = ("twin", "digital twin")
_ALL_CATEGORY_KEYWORDS = _HAZARD_KEYWORDS + _EXPOSURE_KEYWORDS + _RISK_KEYWORDS + _ROUTE_KEYWORDS + _DATASET_KEYWORDS


def _is_twin_only_question(question: str) -> bool:
    """True for a question specifically about the Digital Twin itself
    ("What is the Digital Twin?") with no other specific category
    mentioned -- lets get_twin_state answer it alone, rather than also
    pulling in the broad hazard/exposure/risk/route fallback, which
    would otherwise make this read identically to a generic "what's
    available" question (get_twin_state's own layers/categories already
    answer it).
    """
    q = question.lower()
    return any(keyword in q for keyword in _TWIN_KEYWORDS) and not any(keyword in q for keyword in _ALL_CATEGORY_KEYWORDS)


def _relevant_categories(question: str) -> set[str]:
    """Deterministic keyword match, not a classifier model -- see module
    docstring. When nothing matches, grounds broadly (the four analysis
    categories, not raw datasets) rather than returning no evidence at
    all for a vague question.
    """
    q = question.lower()
    categories: set[str] = set()
    if any(keyword in q for keyword in _HAZARD_KEYWORDS):
        categories.add("hazard")
    if any(keyword in q for keyword in _EXPOSURE_KEYWORDS):
        categories.add("exposure")
    if any(keyword in q for keyword in _RISK_KEYWORDS):
        categories.add("risk")
    if any(keyword in q for keyword in _ROUTE_KEYWORDS):
        categories.add("route")
    if any(keyword in q for keyword in _DATASET_KEYWORDS):
        categories.add("dataset")
    if not categories:
        categories = {"hazard", "exposure", "risk", "route"}
    return categories


def _collect_baseline_evidence(db: Session, request: AssistantQueryRequest, categories: set[str]) -> list[AssistantEvidence]:
    evidence: list[AssistantEvidence] = []
    if "hazard" in categories:
        evidence.append(tools.list_hazard_scenarios(db, request.project_id))
    if "exposure" in categories:
        evidence.append(tools.list_exposure_analyses(db, request.project_id))
    if "risk" in categories:
        risk_list = tools.list_risk_analyses(db, request.project_id)
        evidence.append(risk_list)
        # Directly answers questions like "which areas have the highest
        # landslide risk" end to end: chain into find_highest_risk_classes
        # for the most recently created risk analysis (list_risk_analyses
        # orders by created_at desc, same as every other list_* endpoint).
        if risk_list.status == "success" and any(keyword in request.question.lower() for keyword in _HIGHEST_KEYWORDS):
            items = (risk_list.data or {}).get("items") or []
            if items:
                evidence.append(tools.find_highest_risk_classes(db, items[0]["id"]))
    if "route" in categories:
        evidence.append(tools.list_route_analyses(db, request.project_id))
    if "dataset" in categories:
        evidence.append(tools.list_project_datasets(db, request.project_id))
    return evidence


def _collect_evidence(db: Session, request: AssistantQueryRequest) -> list[AssistantEvidence]:
    evidence: list[AssistantEvidence] = []

    if request.twin_id:
        evidence.append(tools.get_twin_state(db, request.twin_id))

    if request.scenario_id:
        # get_scenario_state already carries every derived analysis for
        # this scenario -- no need to also list project-wide baseline
        # analyses, mirroring DashboardPage.tsx's own baseline-vs-
        # scenario fallback (normalizeScenarioLayers wins over
        # normalizeTwinLayers when a scenario is selected).
        evidence.append(tools.get_scenario_state(db, request.scenario_id))
    elif not (request.twin_id and _is_twin_only_question(request.question)):
        categories = _relevant_categories(request.question)
        evidence.extend(_collect_baseline_evidence(db, request, categories))

    if request.active_feature and request.active_feature.dataset_id:
        evidence.append(tools.get_dataset(db, request.active_feature.dataset_id))

    return evidence[:MAX_TOOL_CALLS]


def answer_question(db: Session, request: AssistantQueryRequest, provider: AIProvider) -> AssistantQueryResponse:
    evidence = _collect_evidence(db, request)

    context: dict[str, object] = {
        "project_id": request.project_id,
        "twin_id": request.twin_id,
        "scenario_id": request.scenario_id,
        "active_feature": request.active_feature.model_dump() if request.active_feature else None,
    }

    result = provider.generate_answer(request.question, context, evidence)

    return AssistantQueryResponse(
        answer=result.answer,
        evidence=evidence,
        unavailable=result.unavailable,
        relevant_limitations=result.relevant_limitations,
    )
