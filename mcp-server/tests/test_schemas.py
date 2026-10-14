"""Schema-Abnahme: 11 Tools, Draft 07, additionalProperties=false (F.1/F.2, spec-focus D.3)."""

from jsonschema import Draft7Validator

from app.tools import TOOL_DEFS, build_tools

EXPECTED_TOOLS = [
    "dashboard_status",
    "dashboard_focus",
    "dashboard_reset",
    "task_list",
    "task_create",
    "task_update",
    "task_delete",
    "screen_off",
    "screen_on",
    "screen_dim",
    "garmin_add_workout",
]


def test_tools_list_has_all_eleven_in_order():
    tools = build_tools()
    assert [tool.name for tool in tools] == EXPECTED_TOOLS
    assert len(tools) == 11


def test_every_input_schema_is_valid_draft7_object():
    for tool in build_tools():
        Draft7Validator.check_schema(tool.input_schema)
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False
        assert tool.description and len(tool.description) > 30


def test_required_fields_are_minimal():
    required = {spec["name"]: spec["inputSchema"].get("required", []) for spec in TOOL_DEFS}
    assert required["dashboard_status"] == []
    assert required["dashboard_focus"] == ["widget_id", "mode"]
    assert required["dashboard_reset"] == []
    assert required["task_list"] == []
    assert required["task_create"] == ["title"]
    assert required["task_update"] == ["task_id"]
    assert required["task_delete"] == ["task_id", "confirm"]
    assert required["screen_off"] == []
    assert required["screen_on"] == []
    assert required["screen_dim"] == []
    assert required["garmin_add_workout"] == ["activity_type", "start_time", "duration_s"]


def test_focus_mode_enum_is_exact():
    spec = next(spec for spec in TOOL_DEFS if spec["name"] == "dashboard_focus")
    mode = spec["inputSchema"]["properties"]["mode"]
    assert mode["enum"] == ["highlight", "enlarge", "fullscreen"]


def test_read_tools_carry_readonly_hint():
    read_tools = {"dashboard_status", "task_list"}
    for tool in build_tools():
        if tool.name in read_tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
        else:
            assert tool.annotations is None or tool.annotations.read_only_hint is not True

