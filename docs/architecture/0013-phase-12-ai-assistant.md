# ADR 0013: Phase 12 AI Assistant (read-only)

Date: 2026-09-25
Status: Accepted

## Context

Phases 0-11 produced a complete analysis pipeline (Data Hub through
Scenario Lab) and a visual Command Dashboard (2D/3D), but no way to ask
a natural-language question about a project's current Digital Twin,
hazard/exposure/risk/route analyses, or scenarios. Phase 12 adds that
assistant, strictly scoped to reading and explaining what CRISIS-X has
already computed -- it introduces no new hazard/exposure/risk/routing
science, and performs no writes of any kind.

## Decision

### Strictly read-only, by construction not convention

`app/services/assistant/tools.py` is the only module in the assistant
package that touches a database session, and every function in it wraps
an *already-existing* `app/api/*.py` endpoint function verbatim (e.g.
`get_digital_twin_state`, `get_scenario_state`,
`create_scenario_comparison`) -- no hazard/exposure/risk/routing/
scenario logic is reimplemented. The orchestrator
(`app/services/assistant/orchestrator.py`) never touches SQL/ORM
directly and never calls a compute/mutate endpoint (dataset upload/
delete, twin-layer registration, scenario mutation, hazard/exposure/
risk/route creation). `tests/test_assistant_api.py::
test_no_write_operation_is_performed_by_a_query` asserts dataset/layer/
analysis counts are identical before and after a battery of assistant
queries -- the read-only guarantee is tested, not just documented.

### Evidence envelope: the anti-hallucination backbone

Every tool call returns an `AssistantEvidence` (`app/schemas/
assistant.py`): `{tool, status: "success"|"unavailable"|"error", data,
limitations, provenance, detail}`. `limitations` is always the
underlying analysis's own disclosure text, copied verbatim (never
paraphrased) from whichever field it already lives in --
`results.limitations` (exposure/risk/route), `provenance.limitations`
(hazard/dataset outputs), or the Digital Twin's own `limitations` list.
A failed lookup (missing id, wrong type, wrong status) is converted from
an `HTTPException` into `status="unavailable"` with the endpoint's own
detail message -- a tool function never raises out to its caller.

### Deterministic tool selection, not a second LLM call

`orchestrator.py`'s `_collect_evidence` decides which tools to call from
a small keyword heuristic over the question text plus whatever
dashboard context was supplied (`twin_id`/`scenario_id`/
`active_feature`) -- not a provider-driven tool-selection loop. This
keeps the orchestrator fully inspectable and testable with no AI
provider at all (see `tests/test_assistant_orchestrator.py`). One
targeted chain is wired in: a "highest/most/worst" risk question first
lists risk analyses, then ranks the most recent one's classes via
`find_highest_risk_classes` (pure re-ordering of already-computed
`by_class` output) -- directly answering the roadmap's own example
question, "Which areas have the highest landslide risk?". Multi-step
chaining for other tools (e.g. auto-pairing two analyses for
`compare_scenario_analyses`) is not implemented in this phase; that tool
exists and is tested, reachable by a future, more capable orchestrator.

### Provider abstraction, no vendor SDK yet

`app/services/assistant/provider.py` defines `AIProvider`
(`generate_answer(question, context, evidence) -> AssistantAnswer`) and
`FakeProvider`, a deterministic, fully offline implementation used by
every automated test and by the API's default dependency
(`app/api/assistant.py::get_assistant_provider`) regardless of whether
`ai_provider_api_key` is configured. No OpenAI/Anthropic/etc. SDK is
added in this phase -- `Settings.ai_provider_api_key`/
`ai_provider_model` (`app/core/config.py`) exist and are tested loadable
both absent and present, but nothing reads them to select a different
provider yet. Wiring a real provider behind the same `AIProvider`
interface is future work, not required to run CRISIS-X locally.

### Frontend: one new dock tab, no new global state system

`AssistantPanel.tsx` slots into the Command Dashboard's existing
`InfoPanelDock` tabs array (`DashboardPage.tsx`) -- no layout
restructuring. It reads `projectId`/`twinId`/`scenarioId`/
`activeFeature` straight from the existing `useDashboardStore`; a new
`useAssistant` hook owns only the per-question answer/evidence/loading
state, matching every other dock panel's local-state pattern. If a map
feature is selected, its `properties` are passed through as optional
context (no new server-side feature-lookup system is introduced).

## Consequences

- No new database tables; `AssistantEvidence`/`AssistantQueryRequest`/
  `AssistantQueryResponse` are transient, not persisted.
- No changes to any Phase 0-11 hazard/exposure/risk/routing/scenario
  algorithm, endpoint response shape, or the Digital Twin/Scenario Lab
  architecture.
- `tests/conftest.py` gained one purely-additive `db_session` fixture
  (a raw `Session` on the same SQLite database the `client` fixture's
  `TestClient` uses) so tool-layer tests can seed data via real HTTP
  calls and then call a tool function directly with a real `Session` --
  every existing test's behavior and the `client` fixture's own
  signature are unchanged.
- The assistant works, and is fully tested, with zero AI configuration
  -- `AI_PROVIDER_API_KEY`/`AI_PROVIDER_MODEL` are optional and commented
  out by default in `apps/api/.env.example`.
