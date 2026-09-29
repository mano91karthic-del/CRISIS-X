"""Phase 12: AI Assistant request/response schemas.

Mirrors the existing schema style (plain Pydantic BaseModels, no custom
base class) used throughout app/schemas/*.py. See
app/services/assistant/tools.py for how AssistantEvidence is produced.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AssistantFeatureContext(BaseModel):
    """Optional context describing the dashboard's currently-selected
    map/vector feature (apps/web/src/state/dashboardStore.ts's
    `activeFeature`) -- passed through by the frontend as-is. Phase 12
    introduces no new server-side feature-lookup system: if the feature
    came from a specific dataset, `dataset_id` lets the assistant look
    that dataset up via the existing get_dataset tool; `properties` are
    whatever attributes MapLibre's click handler already captured.
    """

    dataset_id: str | None = None
    feature_id: str | int | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class AssistantQueryRequest(BaseModel):
    question: str = Field(min_length=1)
    project_id: str
    twin_id: str | None = None
    scenario_id: str | None = None
    active_feature: AssistantFeatureContext | None = None


class AssistantEvidence(BaseModel):
    """One read-only tool call's result, in the uniform envelope every
    assistant tool returns (app/services/assistant/tools.py). The AI
    provider must only state facts present in `data`, and must surface
    `limitations`/`provenance` verbatim rather than paraphrasing them --
    this is the anti-hallucination backbone described in
    docs/architecture (Phase 12 ADR).
    """

    tool: str
    status: Literal["success", "unavailable", "error"]
    data: Any = None
    limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] | None = None
    # Human-readable reason for a non-"success" status (e.g. "Risk
    # analysis not found") -- never fabricated, always the underlying
    # lookup's own message.
    detail: str | None = None


class AssistantQueryResponse(BaseModel):
    answer: str
    evidence: list[AssistantEvidence] = Field(default_factory=list)
    # Short, human-readable descriptions of information the question
    # asked for but that no tool could provide -- distinct from
    # `evidence`'s own unavailable/error items, this is the provider's
    # own summary of what it could NOT answer.
    unavailable: list[str] = Field(default_factory=list)
    # The (small) subset of evidence limitations the provider judged
    # relevant to THIS question -- already woven into `answer`'s prose,
    # and also surfaced here verbatim so the UI can show it as its own
    # labeled section without re-parsing the answer text. Never every
    # limitation across all evidence -- see provider.py's selection logic.
    relevant_limitations: list[str] = Field(default_factory=list)
