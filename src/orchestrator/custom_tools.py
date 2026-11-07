"""Workflow automation and user-defined custom tool registry (Phases 87-88)."""
from typing import Any, Callable, Coroutine, Dict, List, Optional
from pydantic import BaseModel, Field


class CustomToolDefinition(BaseModel):
    name: str = Field(pattern="^[a-zA-Z0-9_-]+$")
    description: str
    parameter_schema: Dict[str, Any]
    risk_tier: str = "MEDIUM"
    requires_human_approval: bool = True


class WorkflowStep(BaseModel):
    step_id: str
    tool_name: str
    arguments: Dict[str, Any]
    description: str


class WorkflowTemplate(BaseModel):
    template_id: str
    name: str
    description: str
    steps: List[WorkflowStep]


class CustomToolRegistry:
    """Registry allowing users to securely define and execute custom tools."""

    def __init__(self):
        self._definitions: Dict[str, CustomToolDefinition] = {}
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Coroutine[Any, Any, Any]]] = {}

    def register_custom_tool(
        self,
        definition: CustomToolDefinition,
        handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, Any]],
    ) -> None:
        """Register custom tool with validation schema."""
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def get_tool(self, name: str) -> Optional[CustomToolDefinition]:
        return self._definitions.get(name)

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        if name not in self._handlers:
            raise KeyError(f"Custom tool '{name}' is not registered.")
        handler = self._handlers[name]
        return await handler(arguments)

    def list_tools(self) -> List[CustomToolDefinition]:
        return list(self._definitions.values())
