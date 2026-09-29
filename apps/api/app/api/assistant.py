from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.project import Project
from app.schemas.assistant import AssistantQueryRequest, AssistantQueryResponse
from app.services.assistant.orchestrator import answer_question
from app.services.assistant.provider import AIProvider, FakeProvider

router = APIRouter(tags=["assistant"])


def get_assistant_provider() -> AIProvider:
    """Dependency-injected so tests can override this with a scripted
    provider (see tests/test_assistant_api.py) without any real AI API
    key, and so a real provider can be swapped in later behind the same
    AIProvider interface without touching this route. Phase 12 ships no
    real vendor integration -- see app/services/assistant/provider.py's
    module docstring -- so this always returns the deterministic
    FakeProvider today, regardless of whether `ai_provider_api_key` is
    configured (see core/config.py).
    """
    return FakeProvider()


@router.post("/projects/{project_id}/assistant/query", response_model=AssistantQueryResponse)
def query_assistant(
    project_id: str,
    payload: AssistantQueryRequest,
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_assistant_provider),
) -> AssistantQueryResponse:
    """Read-only: performs no dataset/scenario/twin mutation. See
    app/services/assistant/orchestrator.py for the full read-only
    boundary this endpoint delegates to.
    """
    if payload.project_id != project_id:
        raise HTTPException(status_code=400, detail="project_id in the request body must match the URL path")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    return answer_question(db, payload, provider)
