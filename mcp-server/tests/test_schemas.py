"""Schema-Abnahme: 9 Tools, Draft 07, additionalProperties=false (F.1/F.2)."""

from jsonschema import Draft7Validator

from app.tools import TOOL_DEFS, build_tools

EXPECTED_TOOLS = [
    "dashboard_status",
    "task_list",
    "task_create",
    "task_update",
    "task_delete",
    "screen_off",
    "screen_on",
    "screen_dim",
    "garmin_add_workout",
]


def test_tools_list_has_all_nine_in_order():
    tools = build_tools()
    assert [tool.name for tool in tools] == EXPECTED_TOOLS
    assert len(tools) == 9


def test_every_input_schema_is_valid_draft7_object():
    for tool in build_tools():
        Draft7Validator.check_schema(tool.input_schema)
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False
        assert tool.description and len(tool.description) > 30


def test_required_fields_are_minimal():
    required = {spec["name"]: spec["inputSchema"].get("required", []) for spec in TOOL_DEFS}
    assert required["dashboard_status"] == []
    assert required["task_list"] == []
    assert required["task_create"] == ["title"]
    assert required["task_update"] == ["task_id"]
    assert required["task_delete"] == ["task_id", "confirm"]
    assert required["screen_off"] == []
    assert required["screen_on"] == []
    assert required["screen_dim"] == []
    assert required["garmin_add_workout"] == ["activity_type", "start_time", "duration_s"]


def test_read_tools_carry_readonly_hint():
    read_tools = {"dashboard_status", "task_list"}
    for tool in build_tools():
        if tool.name in read_tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
