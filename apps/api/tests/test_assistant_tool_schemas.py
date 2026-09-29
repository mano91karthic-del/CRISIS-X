"""Phase 12: coverage test -- the tool schema registry
(app/services/assistant/tool_schemas.py) and the actual tool
implementations (app/services/assistant/tools.py) must never drift
apart, and every registered tool must be explicitly read-only.
"""

from app.services.assistant import tool_schemas, tools


def test_every_tool_has_exactly_one_schema() -> None:
    assert set(tool_schemas.TOOL_SCHEMAS) == set(tools.__all__)


def test_every_schema_name_matches_its_dict_key() -> None:
    for key, schema in tool_schemas.TOOL_SCHEMAS.items():
        assert schema.name == key


def test_every_schema_points_at_a_real_callable() -> None:
    for name in tool_schemas.TOOL_SCHEMAS:
        assert callable(getattr(tools, name, None)), f"tools.{name} is not callable"


def test_no_tool_is_registered_as_an_action_tool() -> None:
    """Phase 12 defines read-only tools only -- see tool_schemas.py's
    module docstring. This is a structural guarantee, not just a
    convention: every schema's read_only flag must be True.
    """
    assert all(schema.read_only for schema in tool_schemas.TOOL_SCHEMAS.values())


def test_every_schema_has_a_purpose_and_well_typed_arguments() -> None:
    for schema in tool_schemas.TOOL_SCHEMAS.values():
        assert schema.purpose.strip()
        for argument in schema.arguments:
            assert argument.name
            assert argument.type
            assert argument.description.strip()
