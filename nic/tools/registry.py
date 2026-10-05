"""Tool registry.

Every capability the assistant has is a plain Python function registered
here with a JSON schema. The schema is what we hand to the model; the
function is what actually runs.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

JsonSchema = dict[str, Any]


class ToolError(Exception):
    """A tool failed in a way worth reporting back to the model."""


class ConfirmationRequired(Exception):
    """Raised when a dangerous tool is called without an approval."""

    def __init__(self, tool_name: str, arguments: dict[str, Any], summary: str):
        super().__init__(summary)
        self.tool_name = tool_name
        self.arguments = arguments
        self.summary = summary


@dataclass
class Tool:
    name: str
    description: str
    parameters: JsonSchema
    func: Callable[..., Any]
    # Dangerous tools are confirmed with the user before they run.
    dangerous: bool = False
    # Which device this touches, used for grouping and for enable checks.
    surface: str = "laptop"

    def schema(self) -> JsonSchema:
        """The tool definition in the OpenAI/Ollama function-calling shape."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def call(self, arguments: dict[str, Any]) -> Any:
        signature = inspect.signature(self.func)
        accepted = set(signature.parameters)
        unexpected = set(arguments) - accepted
        if unexpected:
            raise ToolError(
                f"{self.name} got unexpected argument(s): {', '.join(sorted(unexpected))}"
            )
        missing = [
            name
            for name, param in signature.parameters.items()
            if param.default is inspect.Parameter.empty and name not in arguments
        ]
        if missing:
            raise ToolError(f"{self.name} is missing argument(s): {', '.join(missing)}")
        return self.func(**arguments)


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self.tools[tool.name] = tool
        return tool

    def tool(
        self,
        name: str,
        description: str,
        parameters: JsonSchema | None = None,
        *,
        dangerous: bool = False,
        surface: str = "laptop",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of :meth:`register`."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register(
                Tool(
                    name=name,
                    description=description,
                    parameters=parameters or {"type": "object", "properties": {}},
                    func=func,
                    dangerous=dangerous,
                    surface=surface,
                )
            )
            return func

        return decorator

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise ToolError(f"unknown tool: {name}")
        return self.tools[name]

    def schemas(self) -> list[JsonSchema]:
        return [tool.schema() for tool in self.tools.values()]

    def names(self) -> list[str]:
        return sorted(self.tools)

    def merge(self, other: "ToolRegistry") -> None:
        for tool in other.tools.values():
            self.register(tool)

    def drop(self, names: list[str]) -> None:
        for name in names:
            self.tools.pop(name, None)


def string_param(description: str) -> JsonSchema:
    return {"type": "string", "description": description}


def int_param(description: str) -> JsonSchema:
    return {"type": "integer", "description": description}


def object_schema(properties: dict[str, JsonSchema], required: list[str] | None = None) -> JsonSchema:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }
