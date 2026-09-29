"""Phase 12: unit tests for FakeProvider's conversational response
generation (app/services/assistant/provider.py) -- proves it produces
natural, evidence-grounded answers rather than dumping tool names/raw
evidence, never invents a fact, and surfaces only relevant limitations.
"""

from app.schemas.assistant import AssistantEvidence
from app.services.assistant.provider import FakeProvider

TWIN_LIMITATION = (
    "This Digital Twin is a composed registry of already-modeled/observed CRISIS-X datasets for this "
    "project; it is not a live sensor feed and does not reflect real-time conditions."
)
RISK_LIMITATION = (
    "risk_score is NOT weighted, scaled, or normalized by exposure quantity (count, length_m, area_m2, "
    "population_sum, ...). It must never be read as a total, aggregate, or project-level risk magnitude."
)


def test_no_evidence_produces_a_natural_prompt_to_select_a_project() -> None:
    provider = FakeProvider()
    result = provider.generate_answer("What is the risk here?", {}, [])
    assert "returned data" not in result.answer
    assert result.unavailable


def test_all_unavailable_evidence_is_phrased_naturally_not_as_a_tool_log() -> None:
    provider = FakeProvider()
    evidence = [
        AssistantEvidence(tool="get_risk_analysis", status="unavailable", data=None, detail="Risk analysis not found"),
    ]
    result = provider.generate_answer("What is the highest risk area?", {}, evidence)
    assert "get_risk_analysis" not in result.answer
    assert "returned" not in result.answer
    assert "risk analysis" in result.answer.lower()
    assert result.unavailable


def test_twin_state_evidence_produces_a_conversational_summary() -> None:
    provider = FakeProvider()
    evidence = [
        AssistantEvidence(
            tool="get_twin_state",
            status="success",
            data={
                "layers_by_category": {
                    "terrain": [{"layer": {}, "dataset": {}}],
                    "hazard": [{"layer": {}, "dataset": {}}],
                },
                "limitations": [TWIN_LIMITATION],
            },
            limitations=[TWIN_LIMITATION],
        )
    ]
    result = provider.generate_answer("What is the Digital Twin?", {}, evidence)
    assert "get_twin_state" not in result.answer
    assert "returned data" not in result.answer
    assert "terrain" in result.answer.lower()
    assert "hazard" in result.answer.lower()
    # The "not live" caveat is the relevant one for a twin-flavored question.
    assert "live sensor feed" in result.answer.lower()
    assert result.relevant_limitations == [TWIN_LIMITATION]


def test_highest_risk_evidence_uses_actual_values_and_never_names_a_location() -> None:
    provider = FakeProvider()
    evidence = [
        AssistantEvidence(
            tool="find_highest_risk_classes",
            status="success",
            data={
                "risk_analysis_id": "r1",
                "ranked_classes": [
                    {"hazard_class_label": "very_high", "risk_class": "high", "risk_score": 0.54, "count": 3},
                    {"hazard_class_label": "high", "risk_class": "moderate", "risk_score": 0.31, "count": 5},
                ],
            },
            limitations=[RISK_LIMITATION],
        )
    ]
    result = provider.generate_answer("What are the highest risk classes?", {}, evidence)
    assert "find_highest_risk_classes" not in result.answer
    assert "0.54" in result.answer
    assert "high" in result.answer.lower()
    assert "geographic location" in result.answer.lower()  # never invents a place name
    assert "never be read as" in result.answer.lower()  # the relevant risk-aggregation caveat


def test_risk_question_selects_the_aggregation_limitation_not_every_limitation() -> None:
    provider = FakeProvider()
    evidence = [
        AssistantEvidence(
            tool="get_risk_analysis",
            status="success",
            data={"name": "Landslide risk", "results": {"by_class": {"high": {"risk_class": "high", "risk_score": 0.5}}}},
            limitations=[RISK_LIMITATION, "Some other unrelated caveat."],
        )
    ]
    result = provider.generate_answer("What is the risk?", {}, evidence)
    assert result.relevant_limitations == [RISK_LIMITATION]
    assert "Some other unrelated caveat." not in result.answer


def test_no_limitation_is_appended_when_none_is_relevant_and_none_exists() -> None:
    provider = FakeProvider()
    evidence = [
        AssistantEvidence(tool="get_dataset", status="success", data={"name": "roads.geojson", "dataset_type": "roads", "status": "validated"}, limitations=[])
    ]
    result = provider.generate_answer("Tell me about this dataset", {}, evidence)
    assert result.relevant_limitations == []


def test_mixed_success_and_unavailable_reports_both_conversationally() -> None:
    provider = FakeProvider()
    evidence = [
        AssistantEvidence(
            tool="get_twin_state",
            status="success",
            data={"layers_by_category": {"terrain": [{}]}, "limitations": [TWIN_LIMITATION]},
            limitations=[TWIN_LIMITATION],
        ),
        AssistantEvidence(tool="get_route_analysis", status="unavailable", data=None, detail="Route analysis not found"),
    ]
    result = provider.generate_answer("What is the twin state and the route?", {}, evidence)
    assert "get_route_analysis" not in result.answer
    assert "route" in result.answer.lower()
    assert result.unavailable


def test_error_status_is_treated_the_same_as_unavailable() -> None:
    provider = FakeProvider()
    evidence = [AssistantEvidence(tool="get_dataset", status="error", data=None, detail="unexpected failure")]
    result = provider.generate_answer("Tell me about this dataset", {}, evidence)
    assert "returned" not in result.answer
    assert result.unavailable


def test_broad_question_produces_a_short_overview_not_a_full_dump() -> None:
    provider = FakeProvider()
    evidence = [
        AssistantEvidence(tool="list_hazard_scenarios", status="success", data={"items": [{"name": "a"}], "total_count": 1}, limitations=[]),
        AssistantEvidence(tool="list_exposure_analyses", status="success", data={"items": [{"name": "b"}], "total_count": 1}, limitations=[]),
        AssistantEvidence(tool="list_risk_analyses", status="success", data={"items": [], "total_count": 0}, limitations=[]),
        AssistantEvidence(tool="list_route_analyses", status="success", data={"items": [{"name": "c"}], "total_count": 1}, limitations=[]),
    ]
    result = provider.generate_answer("What can you tell me about this project?", {}, evidence)
    assert "CRISIS-X currently has" in result.answer
    assert "list_hazard_scenarios" not in result.answer
    # Short: an overview, not a per-analysis dump of every list.
    assert len(result.answer) < 400


def test_is_deterministic_for_the_same_input() -> None:
    provider = FakeProvider()
    evidence = [
        AssistantEvidence(tool="get_twin_state", status="success", data={"layers_by_category": {"terrain": [{}]}}, limitations=[])
    ]
    first = provider.generate_answer("question", {}, evidence)
    second = provider.generate_answer("question", {}, evidence)
    assert first == second


def test_never_contains_raw_evidence_dump_phrasing() -> None:
    """Regression for the exact complaint this rework fixes."""
    provider = FakeProvider()
    evidence = [
        AssistantEvidence(tool="get_twin_state", status="success", data={"layers_by_category": {"terrain": [{}]}}, limitations=[TWIN_LIMITATION]),
        AssistantEvidence(tool="list_risk_analyses", status="success", data={"items": [], "total_count": 0}, limitations=[]),
        AssistantEvidence(
            tool="find_highest_risk_classes",
            status="success",
            data={"ranked_classes": [{"hazard_class_label": "high", "risk_class": "moderate", "risk_score": 0.3}]},
            limitations=[RISK_LIMITATION],
        ),
    ]
    result = provider.generate_answer("What are the highest risk classes?", {}, evidence)
    for banned in ("returned data", "get_twin_state", "list_risk_analyses", "find_highest_risk_classes"):
        assert banned not in result.answer
