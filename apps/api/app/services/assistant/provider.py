"""Phase 12: AI provider abstraction.

Defines the seam between the assistant orchestrator (deterministic,
tool-driven) and whatever turns collected evidence into a
natural-language answer. No vendor SDK is added in this phase -- only
this Protocol and a deterministic FakeProvider, which every automated
test uses and which the API dependency-injects by default (see
app/api/assistant.py) regardless of whether `ai_provider_api_key` is
configured (see core/config.py). A real provider is a future addition
behind this same interface -- `generate_answer(question, context,
evidence)` already receives everything a real LLM call would need: the
user's question, dashboard context (project/twin/scenario/active
feature, via `context`), and every collected evidence envelope
(including each item's own `limitations`/`provenance`) -- see the Phase
12 ADR for what plugging one in would require.

FakeProvider does NOT dump tool names or raw evidence objects into the
answer -- see `_SUMMARIZERS` below, one small deterministic
sentence-builder per tool, each reading only fields that are actually
present in that tool's own evidence `data` and never inventing a fact,
number, or location the evidence doesn't contain. Tool names stay
visible in the separate `evidence` list on the response (for UI
transparency), never in `answer`'s prose.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas.assistant import AssistantEvidence

# Anything implementing generate_answer() -- real or fake -- must uphold
# these rules. A future real provider would send this verbatim as (or
# alongside) its system prompt; FakeProvider enforces the same rules
# structurally, by construction, rather than by instruction-following.
ASSISTANT_SYSTEM_INSTRUCTIONS = """
You are the CRISIS-X Digital Twin assistant. You answer ONLY from the
evidence supplied to you for this turn. Rules:
1. Only state facts present in the supplied evidence.
2. Never invent a number, dataset, analysis result, or geographic location not present in the evidence.
3. Never contradict a `limitations` string attached to any evidence item.
4. Clearly distinguish observed data, model-derived output, scenario output, and user-declared assumptions
   (see each evidence item's own provenance/origin fields).
5. If the evidence needed to answer is missing, marked "unavailable", or marked "error", say so explicitly,
   in plain conversational language, instead of guessing.
6. Never claim CRISIS-X has live weather, live satellite, or real-time data -- it does not.
7. Never describe performing an action (registering a layer, running a new analysis, modifying a scenario,
   uploading or deleting a dataset) -- you are strictly read-only.
8. Answer like a project-aware assistant talking to a person, not a debug console: no internal tool names,
   no raw JSON, no repeating every limitation on every turn -- surface only the limitation(s) relevant to
   what was actually asked.
""".strip()


@dataclass(frozen=True)
class AssistantAnswer:
    answer: str
    # Short, human-readable descriptions of what could not be answered --
    # separate from any individual evidence item's own status, this is
    # the provider's own summary of unmet information needs.
    unavailable: list[str] = field(default_factory=list)
    # The (small) subset of evidence limitations judged relevant to this
    # question -- already woven into `answer`'s prose; also returned
    # separately so a UI can show it as its own section. Never every
    # limitation across all evidence -- see _select_relevant_limitations.
    relevant_limitations: list[str] = field(default_factory=list)


class AIProvider(Protocol):
    def generate_answer(
        self, question: str, context: dict[str, object], evidence: list[AssistantEvidence]
    ) -> AssistantAnswer: ...


# --- natural-language helpers (pure, no state) --------------------------------------


def _join_naturally(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _fmt_count(n: int, singular: str, plural: str | None = None) -> str:
    plural = plural or f"{singular}s"
    return f"{n} {singular if n == 1 else plural}"


# Priority order: prefer a feature/asset count over a raw numeric sum,
# since "12 buildings" reads more naturally than "12 units" -- but never
# invents a field that isn't actually present in this analysis's by_class entry.
_QUANTITY_FIELDS: list[tuple[str, str]] = [
    ("feature_count", "features"),
    ("count", "assets"),
    ("length_m", "m of length"),
    ("area_m2", "m² of area"),
    ("population_sum", "people"),
    ("sum", "units"),
]


def _pick_quantity_phrase(entry: dict[str, Any]) -> str | None:
    for field_name, unit in _QUANTITY_FIELDS:
        value = entry.get(field_name)
        if value is None:
            continue
        try:
            return f"{float(value):,.0f} {unit}"
        except (TypeError, ValueError):
            continue
    return None


# --- per-tool summarizers: each reads ONLY that tool's own evidence data ------------


def _summarize_twin_state(data: dict[str, Any]) -> list[str]:
    layers_by_category = data.get("layers_by_category") or {}
    if not layers_by_category:
        return ["The Digital Twin exists for this project but has no layers registered yet."]
    categories = list(layers_by_category.keys())
    layer_count = sum(len(v) for v in layers_by_category.values())
    return [
        f"The Digital Twin currently brings together {_join_naturally(categories)} layers "
        f"({_fmt_count(layer_count, 'registered layer')} in total)."
    ]


def _summarize_scenario_state(data: dict[str, Any]) -> list[str]:
    scenario = data.get("scenario") or {}
    derived = data.get("derived_analyses") or {}
    name = scenario.get("name") or "This scenario"
    counts = {
        "hazard": len(derived.get("hazard_scenarios") or []),
        "exposure": len(derived.get("exposure_analyses") or []),
        "risk": len(derived.get("risk_analyses") or []),
        "route": len(derived.get("route_analyses") or []),
    }
    computed = [f"{v} {k}" for k, v in counts.items() if v > 0]
    if computed:
        return [f"The '{name}' scenario has {_join_naturally(computed)} analysis result(s) computed against it."]
    return [f"The '{name}' scenario exists but has no analyses computed against it yet."]


def _summarize_hazard_list(data: dict[str, Any]) -> list[str]:
    items = data.get("items") or []
    if not items:
        return ["No hazard scenarios have been run for this project yet."]
    names = [f"a {it.get('hazard_type')} scenario ('{it.get('name')}')" for it in items[:3]]
    extra = len(items) - len(names)
    suffix = f", plus {extra} more" if extra > 0 else ""
    return [f"This project has {_fmt_count(len(items), 'hazard scenario')}: {_join_naturally(names)}{suffix}."]


def _summarize_hazard_detail(data: dict[str, Any]) -> list[str]:
    scenario = data.get("scenario") or {}
    return [
        f"The '{scenario.get('name')}' scenario models a {scenario.get('hazard_type')} hazard "
        f"and is currently {scenario.get('status')}."
    ]


def _summarize_exposure_list(data: dict[str, Any]) -> list[str]:
    items = data.get("items") or []
    if not items:
        return ["No exposure analyses have been run for this project yet."]
    parts = [f"'{it.get('name')}' ({it.get('exposure_dataset_type')} exposed to {it.get('hazard_dataset_type')})" for it in items[:3]]
    extra = len(items) - len(parts)
    suffix = f", plus {extra} more" if extra > 0 else ""
    return [f"This project has {_fmt_count(len(items), 'exposure analysis', 'exposure analyses')}: {_join_naturally(parts)}{suffix}."]


def _summarize_exposure_detail(data: dict[str, Any]) -> list[str]:
    results = data.get("results") or {}
    by_class = results.get("by_class") or {}
    if not by_class:
        return [f"The exposure analysis '{data.get('name')}' has not produced a class breakdown yet."]
    parts = []
    for label, entry in list(by_class.items())[:5]:
        qty = _pick_quantity_phrase(entry)
        parts.append(f"{label}: {qty}" if qty else label)
    return [f"Exposure breakdown for '{data.get('name')}' -- {'; '.join(parts)}."]


def _summarize_risk_list(data: dict[str, Any]) -> list[str]:
    items = data.get("items") or []
    if not items:
        return ["No risk analyses have been run for this project yet."]
    parts = [f"'{it.get('name')}'" for it in items[:3]]
    extra = len(items) - len(parts)
    suffix = f", plus {extra} more" if extra > 0 else ""
    return [f"This project has {_fmt_count(len(items), 'risk analysis', 'risk analyses')}: {_join_naturally(parts)}{suffix}."]


def _summarize_risk_detail(data: dict[str, Any]) -> list[str]:
    results = data.get("results") or {}
    by_class = results.get("by_class") or {}
    if not by_class:
        return [f"The risk analysis '{data.get('name')}' has not produced a class breakdown yet."]
    ranked = sorted(by_class.items(), key=lambda kv: kv[1].get("risk_score", float("-inf")), reverse=True)
    parts = [f"{label} ({entry.get('risk_class')}, score {entry.get('risk_score', 0):.2f})" for label, entry in ranked[:5]]
    return [f"Risk classes for '{data.get('name')}': {'; '.join(parts)}."]


def _summarize_highest_risk(data: dict[str, Any]) -> list[str]:
    ranked = data.get("ranked_classes") or []
    if not ranked:
        return ["The risk analysis has no class results to rank."]
    top = ranked[0]
    sentence = (
        f"The highest risk class in the current analysis is '{top.get('risk_class', 'unknown')}' "
        f"(hazard class '{top.get('hazard_class_label', 'unknown')}', risk score {top.get('risk_score', 0):.2f})."
    )
    # Always attached for this tool -- find_highest_risk_classes never has
    # geography, only class labels (see tools.py's own docstring).
    geography_caveat = "The current analysis is class-based; the available result does not provide a named geographic location."
    return [sentence, geography_caveat]


def _summarize_route_list(data: dict[str, Any]) -> list[str]:
    items = data.get("items") or []
    if not items:
        return ["No route analyses have been run for this project yet."]
    parts = [f"'{it.get('name')}'" for it in items[:3]]
    extra = len(items) - len(parts)
    suffix = f", plus {extra} more" if extra > 0 else ""
    return [f"This project has {_fmt_count(len(items), 'route analysis', 'route analyses')}: {_join_naturally(parts)}{suffix}."]


def _summarize_route_detail(data: dict[str, Any]) -> list[str]:
    results = data.get("results") or {}
    hazard_aware = results.get("hazard_aware_route") or {}
    shortest = results.get("shortest_route") or {}
    name = data.get("name")
    if hazard_aware.get("feasible"):
        distance = hazard_aware.get("distance_m")
        distance_phrase = f", covering about {distance:,.0f} m" if isinstance(distance, (int, float)) else ""
        return [f"The hazard-aware route for '{name}' is feasible{distance_phrase}."]
    if shortest.get("feasible"):
        return [f"A hazard-aware route wasn't feasible for '{name}', but a shortest route (without hazard avoidance) exists."]
    return [f"Neither route option was feasible for '{name}' with the current road network and blocking."]


def _summarize_dataset(data: dict[str, Any]) -> list[str]:
    return [f"Dataset '{data.get('name')}' is a {data.get('dataset_type')} dataset, currently {data.get('status')}."]


def _summarize_dataset_geojson(data: dict[str, Any]) -> list[str]:
    total = data.get("total_feature_count", len(data.get("features") or []))
    return [f"{total} feature(s) are available in this dataset."]


def _summarize_dataset_list(data: dict[str, Any]) -> list[str]:
    items = data.get("items") or []
    if not items:
        return ["No datasets have been uploaded to this project yet."]
    parts = [f"'{it.get('name')}' ({it.get('dataset_type')})" for it in items[:5]]
    extra = len(items) - len(parts)
    suffix = f", plus {extra} more" if extra > 0 else ""
    return [f"This project has {_fmt_count(len(items), 'dataset')}: {_join_naturally(parts)}{suffix}."]


def _summarize_comparison(data: dict[str, Any]) -> list[str]:
    del data
    return ["A comparison between the two analyses is available in the evidence below."]


_SUMMARIZERS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "get_twin_state": _summarize_twin_state,
    "get_scenario_state": _summarize_scenario_state,
    "list_hazard_scenarios": _summarize_hazard_list,
    "get_hazard_scenario": _summarize_hazard_detail,
    "list_exposure_analyses": _summarize_exposure_list,
    "get_exposure_analysis": _summarize_exposure_detail,
    "list_risk_analyses": _summarize_risk_list,
    "get_risk_analysis": _summarize_risk_detail,
    "find_highest_risk_classes": _summarize_highest_risk,
    "list_route_analyses": _summarize_route_list,
    "get_route_analysis": _summarize_route_detail,
    "get_dataset": _summarize_dataset,
    "get_dataset_geojson": _summarize_dataset_geojson,
    "list_project_datasets": _summarize_dataset_list,
    "compare_scenario_analyses": _summarize_comparison,
}

_FRIENDLY_TOOL_NAMES: dict[str, str] = {
    "get_twin_state": "the Digital Twin state",
    "get_scenario_state": "that scenario's state",
    "list_hazard_scenarios": "hazard scenario information",
    "get_hazard_scenario": "that hazard scenario",
    "list_exposure_analyses": "exposure analysis information",
    "get_exposure_analysis": "that exposure analysis",
    "list_risk_analyses": "risk analysis information",
    "get_risk_analysis": "that risk analysis",
    "find_highest_risk_classes": "a risk analysis to rank",
    "list_route_analyses": "route analysis information",
    "get_route_analysis": "that route analysis",
    "get_dataset": "that dataset",
    "get_dataset_geojson": "that dataset's features",
    "list_project_datasets": "the project's dataset list",
    "compare_scenario_analyses": "a comparison between those analyses",
}

# A question containing any of these keywords selects a specific
# relevant limitation (by locating the real limitation string that
# contains this substring) instead of the default/first one -- see
# _select_relevant_limitations. All substrings are matched against the
# EXISTING limitations text already in evidence, never invented here.
_LIMITATION_TRIGGERS: list[tuple[tuple[str, ...], str]] = [
    (("live", "real-time", "real time", "right now", "currently happening", "current condition"), "live sensor feed"),
    (("damage", "loss", "monetary", "cost", "dollar"), "structural damage"),
    (("predict", "forecast", "will happen", "going to occur", "when will"), "prediction"),
    (("risk",), "never be read as"),
]

_BASELINE_LIST_TOOLS = {"list_hazard_scenarios", "list_exposure_analyses", "list_risk_analyses", "list_route_analyses"}

_CATEGORY_LABELS = {
    "list_hazard_scenarios": "hazard",
    "list_exposure_analyses": "exposure",
    "list_risk_analyses": "risk",
    "list_route_analyses": "routing",
}


def _select_relevant_limitations(question: str, successes: list[AssistantEvidence]) -> list[str]:
    """Picks the small subset of already-collected limitations relevant
    to THIS question -- never all of them (see FakeProvider's docstring
    and ASSISTANT_SYSTEM_INSTRUCTIONS rule 8). Every string returned is
    copied verbatim from an evidence item's own `limitations`.
    """
    all_limitations: list[str] = []
    seen: set[str] = set()
    for item in successes:
        for lim in item.limitations:
            if lim not in seen:
                all_limitations.append(lim)
                seen.add(lim)
    if not all_limitations:
        return []

    q = question.lower()
    for keywords, substring in _LIMITATION_TRIGGERS:
        if any(keyword in q for keyword in keywords):
            match = next((lim for lim in all_limitations if substring.lower() in lim.lower()), None)
            if match:
                return [match]

    # Default for a Digital-Twin-flavored question: its own "not live" caveat.
    twin_item = next((item for item in successes if item.tool == "get_twin_state"), None)
    if twin_item:
        match = next((lim for lim in twin_item.limitations if "live sensor feed" in lim.lower()), None)
        if match:
            return [match]

    return [all_limitations[0]]


def _looks_like_broad_overview(successes: list[AssistantEvidence]) -> bool:
    """True when the orchestrator fell back to its broad "no specific
    category matched" grounding (see orchestrator.py's
    `_relevant_categories`) -- i.e. most of the four baseline list
    tools were called together, meaning the question was general rather
    than about one specific category.
    """
    present = {item.tool for item in successes}
    return len(present & _BASELINE_LIST_TOOLS) >= 3


def _general_overview_sentence(successes: list[AssistantEvidence]) -> str:
    twin_item = next((item for item in successes if item.tool == "get_twin_state"), None)
    categories: list[str] = []
    if twin_item and isinstance(twin_item.data, dict):
        categories = list(twin_item.data.get("layers_by_category", {}).keys())
    if not categories:
        for item in successes:
            label = _CATEGORY_LABELS.get(item.tool)
            data = item.data if isinstance(item.data, dict) else {}
            if label and data.get("total_count", 0) > 0:
                categories.append(label)
    if not categories:
        return "CRISIS-X doesn't have any hazard, exposure, risk, or routing information computed for this project yet."
    return f"CRISIS-X currently has {_join_naturally(categories)} information available for this project."


def _suggest_next_step(broad: bool) -> str | None:
    if not broad:
        return None
    return "I can break down the current hazard, exposure, risk, or route results in more detail if you'd like."


class FakeProvider:
    """Deterministic, fully offline stand-in for a real AI provider.

    Used by every automated test, and by the API by default (Phase 12
    ships no real vendor integration -- see this module's docstring).
    Never calls out to any network, and always produces the same answer
    for the same question/evidence. Reads actual values out of each
    evidence item's `data` (see `_SUMMARIZERS`) rather than describing
    that a tool "returned data" -- and surfaces only the limitation(s)
    relevant to the question, never every limitation on every turn.
    """

    def generate_answer(
        self, question: str, context: dict[str, object], evidence: list[AssistantEvidence]
    ) -> AssistantAnswer:
        del context  # a real provider would use richer context; the fake only inspects evidence + question text

        if not evidence:
            return AssistantAnswer(
                answer="I don't have a project, twin, or scenario selected to work from yet -- choose one and ask again.",
                unavailable=["no evidence collected"],
            )

        successes = [item for item in evidence if item.status == "success"]
        failures = [item for item in evidence if item.status != "success"]

        unavailable_phrases: list[str] = []
        seen_unavail: set[str] = set()
        for item in failures:
            phrase = _FRIENDLY_TOOL_NAMES.get(item.tool, "that information")
            if phrase not in seen_unavail:
                unavailable_phrases.append(phrase)
                seen_unavail.add(phrase)

        broad = _looks_like_broad_overview(successes)

        fact_sentences: list[str] = []
        if broad:
            fact_sentences.append(_general_overview_sentence(successes))
        else:
            seen_facts: set[str] = set()
            for item in successes:
                handler = _SUMMARIZERS.get(item.tool)
                if handler is None:
                    continue
                data = item.data if isinstance(item.data, dict) else {}
                for sentence in handler(data):
                    if sentence not in seen_facts:
                        fact_sentences.append(sentence)
                        seen_facts.add(sentence)

        if not fact_sentences:
            if unavailable_phrases:
                answer = (
                    f"I don't currently have {_join_naturally(unavailable_phrases)} for this project, "
                    "so I can't answer that from the available data."
                )
            else:
                answer = "I don't have enough information yet to answer that -- nothing relevant has been computed for this project so far."
            return AssistantAnswer(answer=answer, unavailable=unavailable_phrases)

        relevant_limitations = _select_relevant_limitations(question, successes)

        answer_parts = list(fact_sentences)
        answer_parts.extend(relevant_limitations)
        if unavailable_phrases:
            answer_parts.append(f"I don't currently have {_join_naturally(unavailable_phrases)} for this project.")
        suggestion = _suggest_next_step(broad)
        if suggestion:
            answer_parts.append(suggestion)

        return AssistantAnswer(
            answer=" ".join(answer_parts),
            unavailable=unavailable_phrases,
            relevant_limitations=relevant_limitations,
        )
