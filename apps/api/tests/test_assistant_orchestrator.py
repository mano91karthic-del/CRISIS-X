"""Phase 12: orchestrator tests
(app/services/assistant/orchestrator.py) -- proves tool-call selection
and evidence assembly are deterministic and testable using only the
FakeProvider, no real AI provider or network access. Reuses the same
full-pipeline seeding helper as test_assistant_tools.py.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.schemas.assistant import AssistantFeatureContext, AssistantQueryRequest
from app.services.assistant.orchestrator import MAX_TOOL_CALLS, answer_question
from app.services.assistant.provider import FakeProvider
from tests.test_assistant_tools import _seed_full_pipeline


def test_answer_question_with_twin_and_scenario_context_grounds_on_both(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    request = AssistantQueryRequest(
        question="What is the current state of this twin?",
        project_id=seed["project_id"],
        twin_id=seed["twin"]["id"],
        scenario_id=seed["scenario"]["id"],
    )
    response = answer_question(db_session, request, FakeProvider())

    tool_names = [item.tool for item in response.evidence]
    assert "get_twin_state" in tool_names
    assert "get_scenario_state" in tool_names
    assert all(item.status == "success" for item in response.evidence)
    assert response.answer  # non-empty
    assert response.unavailable == []


def test_answer_question_without_scenario_falls_back_to_baseline_listings(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    request = AssistantQueryRequest(
        question="What hazards, exposure, risk, and routes are available?",
        project_id=seed["project_id"],
        twin_id=None,
        scenario_id=None,
    )
    response = answer_question(db_session, request, FakeProvider())

    tool_names = [item.tool for item in response.evidence]
    assert "list_hazard_scenarios" in tool_names
    assert "list_exposure_analyses" in tool_names
    assert "list_risk_analyses" in tool_names
    assert "list_route_analyses" in tool_names
    assert "get_scenario_state" not in tool_names


def test_highest_risk_question_chains_into_find_highest_risk_classes(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    request = AssistantQueryRequest(
        question="Which areas have the highest landslide risk?",
        project_id=seed["project_id"],
    )
    response = answer_question(db_session, request, FakeProvider())

    tool_names = [item.tool for item in response.evidence]
    assert "list_risk_analyses" in tool_names
    assert "find_highest_risk_classes" in tool_names
    ranked_evidence = next(item for item in response.evidence if item.tool == "find_highest_risk_classes")
    assert ranked_evidence.status == "success"
    assert ranked_evidence.data["ranked_classes"]


def test_unrelated_question_grounds_broadly_rather_than_returning_nothing(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    request = AssistantQueryRequest(question="hello there", project_id=seed["project_id"])
    response = answer_question(db_session, request, FakeProvider())
    assert len(response.evidence) > 0


def test_missing_twin_produces_unavailable_evidence_not_an_exception(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    request = AssistantQueryRequest(
        question="What is the twin state?", project_id=seed["project_id"], twin_id="does-not-exist"
    )
    response = answer_question(db_session, request, FakeProvider())
    twin_evidence = next(item for item in response.evidence if item.tool == "get_twin_state")
    assert twin_evidence.status == "unavailable"
    assert response.unavailable
    assert "get_twin_state" not in response.answer


def test_no_analyses_yet_produces_unavailable_information_not_a_fabricated_number(
    client: TestClient, db_session: Session
) -> None:
    """No risk analysis exists at all for a brand-new project -- the
    answer must say so, never invent a risk figure. Mirrors the user's
    own example: "What is the highest risk area?" when nothing has been
    computed yet.
    """
    project_id = client.post("/projects", json={"name": "Empty Project"}).json()["id"]
    request = AssistantQueryRequest(question="What is the highest risk area?", project_id=project_id)
    response = answer_question(db_session, request, FakeProvider())

    risk_list_evidence = next(item for item in response.evidence if item.tool == "list_risk_analyses")
    assert risk_list_evidence.status == "success"
    assert risk_list_evidence.data["total_count"] == 0
    # No risk analyses exist, so find_highest_risk_classes is never chained in.
    assert not any(item.tool == "find_highest_risk_classes" for item in response.evidence)


def test_dataset_question_triggers_list_project_datasets_not_broad_grounding(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    request = AssistantQueryRequest(question="What datasets are available?", project_id=seed["project_id"])
    response = answer_question(db_session, request, FakeProvider())

    tool_names = [item.tool for item in response.evidence]
    assert "list_project_datasets" in tool_names
    # A dataset-specific question shouldn't also pull hazard/exposure/risk/route.
    assert "list_hazard_scenarios" not in tool_names
    dataset_evidence = next(item for item in response.evidence if item.tool == "list_project_datasets")
    assert dataset_evidence.status == "success"
    assert dataset_evidence.data["total_count"] >= 2


def test_active_feature_context_triggers_a_dataset_lookup(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    request = AssistantQueryRequest(
        question="Tell me about this feature",
        project_id=seed["project_id"],
        active_feature=AssistantFeatureContext(dataset_id=seed["roads"]["id"], feature_id=0, properties={"name": "Main Rd"}),
    )
    response = answer_question(db_session, request, FakeProvider())
    dataset_evidence = next(item for item in response.evidence if item.tool == "get_dataset")
    assert dataset_evidence.status == "success"
    assert dataset_evidence.data["id"] == seed["roads"]["id"]


def test_evidence_never_exceeds_max_tool_calls(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    request = AssistantQueryRequest(
        question="highest hazard exposure risk route evacuation",
        project_id=seed["project_id"],
        twin_id=seed["twin"]["id"],
        active_feature=AssistantFeatureContext(dataset_id=seed["dem"]["id"]),
    )
    response = answer_question(db_session, request, FakeProvider())
    assert len(response.evidence) <= MAX_TOOL_CALLS
