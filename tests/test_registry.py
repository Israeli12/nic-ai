import pytest

from nic.tools.registry import ToolError, ToolRegistry, object_schema, string_param


def build() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.tool(
        "greet",
        "Say hello",
        object_schema({"name": string_param("who")}, ["name"]),
    )
    def greet(name: str, loud: bool = False) -> str:
        return f"HELLO {name}" if loud else f"hello {name}"

    @registry.tool("boom", "Always fails", dangerous=True)
    def boom() -> str:
        raise ToolError("nope")

    return registry


def test_schema_matches_function_calling_shape():
    schema = build().get("greet").schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "greet"
    assert schema["function"]["parameters"]["required"] == ["name"]


def test_call_uses_defaults_and_rejects_unknown_arguments():
    tool = build().get("greet")
    assert tool.call({"name": "nic"}) == "hello nic"
    assert tool.call({"name": "nic", "loud": True}) == "HELLO nic"
    with pytest.raises(ToolError, match="unexpected argument"):
        tool.call({"name": "nic", "colour": "red"})


def test_call_reports_missing_required_arguments():
    with pytest.raises(ToolError, match="missing argument"):
        build().get("greet").call({})


def test_unknown_tool_raises():
    with pytest.raises(ToolError, match="unknown tool"):
        build().get("nope")


def test_duplicate_registration_is_rejected():
    registry = build()
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(registry.get("greet"))


def test_merge_and_drop():
    registry = build()
    other = ToolRegistry()

    @other.tool("extra", "extra tool")
    def extra() -> str:
        return "x"

    registry.merge(other)
    assert "extra" in registry.names()
    registry.drop(["extra", "not-there"])
    assert "extra" not in registry.names()
