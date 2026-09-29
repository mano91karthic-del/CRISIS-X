"""Phase 12: structured, read-only tool registry.

Describes every tool in app/services/assistant/tools.py by name,
purpose, and argument shape -- this is what an AI provider abstraction
would be given to know what it's allowed to call. No action/compute
tools are defined here: Phase 12's assistant is strictly read-only (see
tools.py's module docstring). A coverage test
(tests/test_assistant_tool_schemas.py) asserts this registry and
tools.__all__ never drift apart.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolArgument:
    name: str
    type: str  # provider-agnostic: "string" | "integer" | "string | null" ...
    required: bool
    description: str


@dataclass(frozen=True)
class ToolSchema:
    name: str
    purpose: str
    arguments: list[ToolArgument] = field(default_factory=list)
    read_only: bool = True


TOOL_SCHEMAS: dict[str, ToolSchema] = {
    "get_twin_state": ToolSchema(
        name="get_twin_state",
        purpose=(
            "Fetch a Digital Twin's full composed state: layers grouped by category, spatial extent, "
            "acquisition date range, missing recommended layers, and standing limitations."
        ),
        arguments=[ToolArgument("twin_id", "string", True, "Digital Twin id")],
    ),
    "get_scenario_state": ToolSchema(
        name="get_scenario_state",
        purpose=(
            "Fetch a Scenario's effective layers (override-wins-over-baseline) plus every hazard/exposure/"
            "risk/route analysis run against it."
        ),
        arguments=[ToolArgument("scenario_id", "string", True, "Scenario id")],
    ),
    "compare_scenario_analyses": ToolSchema(
        name="compare_scenario_analyses",
        purpose="Compare two already-completed analyses of the same type (exposure/risk/route) via the existing Scenario Lab comparison logic.",
        arguments=[
            ToolArgument("analysis_type", "string", True, "One of 'exposure' | 'risk' | 'route'"),
            ToolArgument("left_analysis_id", "string", True, "First analysis id to compare"),
            ToolArgument("right_analysis_id", "string", True, "Second analysis id to compare"),
        ],
    ),
    "list_hazard_scenarios": ToolSchema(
        name="list_hazard_scenarios",
        purpose="List a project's hazard scenarios (flood/landslide), optionally filtered by hazard_type.",
        arguments=[
            ToolArgument("project_id", "string", True, "Project id"),
            ToolArgument("hazard_type", "string | null", False, "Optional filter: 'flood' | 'landslide'"),
            ToolArgument("limit", "integer", False, "Max items to return (default 20)"),
        ],
    ),
    "get_hazard_scenario": ToolSchema(
        name="get_hazard_scenario",
        purpose="Fetch a single hazard scenario's parameters/status plus its output dataset(s), including class legend and limitations.",
        arguments=[ToolArgument("scenario_id", "string", True, "Hazard scenario id")],
    ),
    "list_exposure_analyses": ToolSchema(
        name="list_exposure_analyses",
        purpose="List a project's exposure analyses, optionally filtered by exposure_dataset_type.",
        arguments=[
            ToolArgument("project_id", "string", True, "Project id"),
            ToolArgument("exposure_dataset_type", "string | null", False, "Optional filter, e.g. 'buildings' | 'roads' | 'population'"),
            ToolArgument("limit", "integer", False, "Max items to return (default 20)"),
        ],
    ),
    "get_exposure_analysis": ToolSchema(
        name="get_exposure_analysis",
        purpose="Fetch a single exposure analysis's results (by_class breakdown, hazard_class_labels, limitations).",
        arguments=[ToolArgument("analysis_id", "string", True, "Exposure analysis id")],
    ),
    "list_risk_analyses": ToolSchema(
        name="list_risk_analyses",
        purpose="List a project's risk analyses, optionally filtered by hazard_dataset_type and/or exposure_dataset_type.",
        arguments=[
            ToolArgument("project_id", "string", True, "Project id"),
            ToolArgument("hazard_dataset_type", "string | null", False, "Optional hazard-type filter"),
            ToolArgument("exposure_dataset_type", "string | null", False, "Optional exposure-type filter"),
            ToolArgument("limit", "integer", False, "Max items to return (default 20)"),
        ],
    ),
    "get_risk_analysis": ToolSchema(
        name="get_risk_analysis",
        purpose="Fetch a single risk analysis's results (by_class risk_score/risk_class breakdown, weights used, limitations).",
        arguments=[ToolArgument("analysis_id", "string", True, "Risk analysis id")],
    ),
    "find_highest_risk_classes": ToolSchema(
        name="find_highest_risk_classes",
        purpose=(
            "Rank an already-computed risk analysis's classes by risk_score, highest first. Pure re-ordering "
            "of existing output -- computes no new score, and risk_score must never be read as a city-wide "
            "aggregate (see the returned limitations)."
        ),
        arguments=[
            ToolArgument("risk_analysis_id", "string", True, "Risk analysis id"),
            ToolArgument("top_n", "integer", False, "How many top classes to return (default 5)"),
        ],
    ),
    "list_route_analyses": ToolSchema(
        name="list_route_analyses",
        purpose="List a project's route analyses, optionally filtered by hazard_dataset_type.",
        arguments=[
            ToolArgument("project_id", "string", True, "Project id"),
            ToolArgument("hazard_dataset_type", "string | null", False, "Optional hazard-type filter"),
            ToolArgument("limit", "integer", False, "Max items to return (default 20)"),
        ],
    ),
    "get_route_analysis": ToolSchema(
        name="get_route_analysis",
        purpose="Fetch a single route analysis's results (shortest vs hazard-aware route, feasibility, graph_summary, limitations).",
        arguments=[ToolArgument("analysis_id", "string", True, "Route analysis id")],
    ),
    "get_dataset": ToolSchema(
        name="get_dataset",
        purpose="Fetch a dataset's metadata, provenance, and limitations by id.",
        arguments=[ToolArgument("dataset_id", "string", True, "Dataset id")],
    ),
    "get_dataset_geojson": ToolSchema(
        name="get_dataset_geojson",
        purpose="Fetch a vector dataset's WGS84 GeoJSON features (capped for context size; truncation is always reported).",
        arguments=[
            ToolArgument("dataset_id", "string", True, "Dataset id (must be a vector format)"),
            ToolArgument("limit", "integer", False, "Max features to return (default 200)"),
        ],
    ),
    "list_project_datasets": ToolSchema(
        name="list_project_datasets",
        purpose="List a project's datasets (name, type, status), optionally filtered by dataset_type.",
        arguments=[
            ToolArgument("project_id", "string", True, "Project id"),
            ToolArgument("dataset_type", "string | null", False, "Optional filter, e.g. 'dem' | 'roads' | 'buildings'"),
            ToolArgument("limit", "integer", False, "Max items to return (default 20)"),
        ],
    ),
}
