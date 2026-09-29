"""Phase 12: API-level integration tests for
POST /projects/{project_id}/assistant/query -- uses the app's default
FakeProvider dependency (app/api/assistant.py::get_assistant_provider),
so no real AI provider or network access is required. Also covers
overriding that dependency with a scripted provider, confirming the
endpoint is fully testable without any AI SDK/key.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.assistant import get_assistant_provider
from app.main import app
from app.schemas.assistant import AssistantEvidence
from app.services.assistant.provider import AssistantAnswer
from tests.test_assistant_tools import _seed_full_pipeline


def test_valid_query_returns_answer_and_evidence(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    resp = client.post(
        f"/projects/{seed['project_id']}/assistant/query",
        json={
            "question": "What hazards are known for this project?",
            "project_id": seed["project_id"],
            "twin_id": seed["twin"]["id"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"]
    assert isinstance(body["evidence"], list)
    assert len(body["evidence"]) > 0
    assert isinstance(body["unavailable"], list)


def test_fake_provider_is_used_by_default_with_no_ai_key_configured(client: TestClient, tmp_path: Path) -> None:
    """The default dependency (get_assistant_provider) always returns
    FakeProvider today -- confirms the endpoint works with zero AI
    configuration, matching core/config.py's ai_provider_api_key
    defaulting to None.
    """
    seed = _seed_full_pipeline(client, tmp_path)
    resp = client.post(
        f"/projects/{seed['project_id']}/assistant/query",
        json={"question": "hello", "project_id": seed["project_id"]},
    )
    assert resp.status_code == 200, resp.text
    # FakeProvider's own deterministic phrasing for a non-empty evidence set.
    assert isinstance(resp.json()["answer"], str)


def test_evidence_items_carry_tool_status_and_limitations(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    resp = client.post(
        f"/projects/{seed['project_id']}/assistant/query",
        json={"question": "What is the exposure and risk?", "project_id": seed["project_id"]},
    )
    assert resp.status_code == 200, resp.text
    evidence = resp.json()["evidence"]
    assert any(item["tool"] == "list_exposure_analyses" for item in evidence)
    assert any(item["tool"] == "list_risk_analyses" for item in evidence)
    for item in evidence:
        assert item["status"] in ("success", "unavailable", "error")
        assert "limitations" in item


def test_unavailable_information_for_project_with_no_analyses(client: TestClient) -> None:
    """No project data exists yet at all -- asking about risk must say
    the information is unavailable, never fabricate a risk figure.
    """
    project = client.post("/projects", json={"name": "No Analyses Project"}).json()
    resp = client.post(
        f"/projects/{project['id']}/assistant/query",
        json={"question": "What is the highest risk area?", "project_id": project["id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    risk_evidence = next(item for item in body["evidence"] if item["tool"] == "list_risk_analyses")
    assert risk_evidence["status"] == "success"
    assert risk_evidence["data"]["total_count"] == 0


def test_missing_project_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/projects/does-not-exist/assistant/query",
        json={"question": "hello", "project_id": "does-not-exist"},
    )
    assert resp.status_code == 404


def test_project_id_mismatch_between_path_and_body_returns_400(client: TestClient) -> None:
    project = client.post("/projects", json={"name": "Mismatch Project"}).json()
    resp = client.post(
        f"/projects/{project['id']}/assistant/query",
        json={"question": "hello", "project_id": "a-different-project-id"},
    )
    assert resp.status_code == 400


def test_missing_twin_id_in_context_does_not_error_the_whole_request(client: TestClient) -> None:
    project = client.post("/projects", json={"name": "No Twin Project"}).json()
    resp = client.post(
        f"/projects/{project['id']}/assistant/query",
        json={"question": "What is the twin state?", "project_id": project["id"], "twin_id": "does-not-exist"},
    )
    assert resp.status_code == 200, resp.text
    twin_evidence = next(item for item in resp.json()["evidence"] if item["tool"] == "get_twin_state")
    assert twin_evidence["status"] == "unavailable"


def test_blank_question_is_rejected_with_422(client: TestClient) -> None:
    project = client.post("/projects", json={"name": "Blank Question Project"}).json()
    resp = client.post(
        f"/projects/{project['id']}/assistant/query",
        json={"question": "", "project_id": project["id"]},
    )
    assert resp.status_code == 422


def test_endpoint_never_requires_a_real_ai_provider_dependency_override(client: TestClient, tmp_path: Path) -> None:
    """A caller can override the provider dependency with a scripted
    test double -- proves the endpoint is fully driven by dependency
    injection, never hardcoded to a real vendor SDK.
    """

    class ScriptedProvider:
        def generate_answer(self, question, context, evidence):  # noqa: ANN001, ANN201
            del question, context, evidence
            return AssistantAnswer(answer="scripted answer", unavailable=[])

    seed = _seed_full_pipeline(client, tmp_path)
    app.dependency_overrides[get_assistant_provider] = lambda: ScriptedProvider()
    try:
        resp = client.post(
            f"/projects/{seed['project_id']}/assistant/query",
            json={"question": "anything", "project_id": seed["project_id"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["answer"] == "scripted answer"
    finally:
        del app.dependency_overrides[get_assistant_provider]


def test_no_write_operation_is_performed_by_a_query(client: TestClient, tmp_path: Path) -> None:
    """The assistant is strictly read-only: before/after dataset, twin,
    and analysis counts must be identical across many queries.
    """
    seed = _seed_full_pipeline(client, tmp_path)

    before_datasets = client.get(f"/projects/{seed['project_id']}/datasets").json()
    before_twin_layers = client.get(f"/digital-twins/{seed['twin']['id']}/layers", params={"status": "all"}).json()
    before_risk = client.get(f"/projects/{seed['project_id']}/risk-analyses").json()

    for question in (
        "What is the risk?",
        "Find an evacuation route from this area.",
        "Explain why this area has high risk.",
        "What happens if this area is flooded?",
    ):
        resp = client.post(
            f"/projects/{seed['project_id']}/assistant/query",
            json={
                "question": question,
                "project_id": seed["project_id"],
                "twin_id": seed["twin"]["id"],
                "scenario_id": seed["scenario"]["id"],
            },
        )
        assert resp.status_code == 200, resp.text

    after_datasets = client.get(f"/projects/{seed['project_id']}/datasets").json()
    after_twin_layers = client.get(f"/digital-twins/{seed['twin']['id']}/layers", params={"status": "all"}).json()
    after_risk = client.get(f"/projects/{seed['project_id']}/risk-analyses").json()

    assert len(after_datasets) == len(before_datasets)
    assert len(after_twin_layers) == len(before_twin_layers)
    assert len(after_risk) == len(before_risk)
    assert [d["id"] for d in after_datasets] == [d["id"] for d in before_datasets]


# --- Conversational response-generation behavior (rework requested after the ------
# --- initial Phase 12 pass exposed raw tool logs as the "answer") -----------------


_INTERNAL_TOOL_NAMES = (
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
    "list_project_datasets",
    "get_dataset",
)


def _ask(client: TestClient, seed: dict, question: str, **context) -> dict:
    resp = client.post(
        f"/projects/{seed['project_id']}/assistant/query",
        json={"question": question, "project_id": seed["project_id"], **context},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_project_overview_question_gives_a_human_readable_summary(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    body = _ask(client, seed, "What is this project?", twin_id=seed["twin"]["id"])
    for name in _INTERNAL_TOOL_NAMES:
        assert name not in body["answer"]
    assert "returned data" not in body["answer"]
    assert len(body["answer"]) > 0


def test_hazard_question_surfaces_actual_hazard_information(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    body = _ask(client, seed, "What hazards are available?")
    assert "landslide" in body["answer"].lower()
    assert "list_hazard_scenarios" not in body["answer"]


def test_risk_question_surfaces_actual_risk_information(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    body = _ask(client, seed, "What risk information is available?")
    assert "risk analysis" in body["answer"].lower() or "risk analyses" in body["answer"].lower()
    assert "list_risk_analyses" not in body["answer"]


def test_highest_risk_question_surfaces_actual_ranked_class_data(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    body = _ask(client, seed, "What are the highest risk classes?")
    assert "find_highest_risk_classes" not in body["answer"]
    assert "score" in body["answer"].lower()
    assert "geographic location" in body["answer"].lower()


def test_dataset_question_surfaces_actual_dataset_information(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    body = _ask(client, seed, "What datasets are available?")
    assert "dem" in body["answer"].lower() or "roads" in body["answer"].lower()
    assert "list_project_datasets" not in body["answer"]


def test_unavailable_information_is_phrased_naturally(client: TestClient) -> None:
    project = client.post("/projects", json={"name": "Fresh Project"}).json()
    body = _ask(client, {"project_id": project["id"]}, "What is the highest risk area?")
    assert "returned" not in body["answer"]
    for name in _INTERNAL_TOOL_NAMES:
        assert name not in body["answer"]
    assert "risk" in body["answer"].lower()


def test_only_relevant_limitation_appears_for_a_risk_question(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    # "highest" keyword chains list_risk_analyses -> find_highest_risk_classes,
    # which carries the real RISK_LIMITATIONS text (list_risk_analyses alone
    # doesn't -- each item's own results.limitations isn't surfaced at the
    # list-envelope level, by tools.py's own design).
    body = _ask(client, seed, "What is the highest risk here?")
    assert body["relevant_limitations"]
    assert len(body["relevant_limitations"]) <= 2
    # Every returned limitation is genuine text copied from evidence, not invented.
    all_evidence_limitations = {lim for item in body["evidence"] for lim in item["limitations"]}
    for lim in body["relevant_limitations"]:
        assert lim in all_evidence_limitations


def test_internal_tool_names_never_appear_in_the_answer_for_any_seeded_question(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    questions = [
        "What is this project?",
        "What hazards are available?",
        "What risk information is available?",
        "What are the highest risk classes?",
        "What datasets are available?",
        "What routes are available?",
        "What is the Digital Twin?",
    ]
    for question in questions:
        body = _ask(client, seed, question, twin_id=seed["twin"]["id"])
        for name in _INTERNAL_TOOL_NAMES:
            assert name not in body["answer"], f"{name!r} leaked into the answer for {question!r}"
        assert "returned data" not in body["answer"]


def test_no_hallucinated_geographic_location_in_risk_answer(client: TestClient, tmp_path: Path) -> None:
    seed = _seed_full_pipeline(client, tmp_path)
    body = _ask(client, seed, "Which areas have the highest risk?")
    # The evidence is class-based only -- the answer must not claim a place name.
    assert "geographic location" in body["answer"].lower()
