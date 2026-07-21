import json
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol, cast
from uuid import UUID

from workhub.context import RecallService
from workhub.domain import ActorContext, ToolDefinition


class RuntimeTool(Protocol):
    definition: ToolDefinition

    async def execute(self, arguments: dict[str, Any]) -> str: ...


class RuntimeToolProvider(Protocol):
    async def snapshot(self, actor: ActorContext, session_id: UUID) -> tuple[RuntimeTool, ...]: ...


class CompositeRuntimeToolProvider:
    def __init__(self, *providers: RuntimeToolProvider) -> None:
        self.providers = providers

    async def snapshot(self, actor: ActorContext, session_id: UUID) -> tuple[RuntimeTool, ...]:
        tools: list[RuntimeTool] = []
        for provider in self.providers:
            tools.extend(await provider.snapshot(actor, session_id))
        names = [tool.definition.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("Runtime tool names must be unique")
        return tuple(tools)


@dataclass(frozen=True, slots=True)
class RecallRuntimeTool:
    recall: RecallService
    actor: ActorContext
    session_id: UUID
    definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="recall_session_history",
        description="Expand or search verbatim history for the current employee session.",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["expand", "search"]},
                "seq_start": {"type": "integer", "minimum": 1},
                "seq_end": {"type": "integer", "minimum": 1},
                "query": {"type": "string", "minLength": 1},
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
    )

    async def execute(self, arguments: dict[str, Any]) -> str:
        result = await self.recall.execute(
            self.actor,
            self.session_id,
            mode=cast(Literal["expand", "search"], arguments.get("mode")),
            seq_start=arguments.get("seq_start"),
            seq_end=arguments.get("seq_end"),
            query=arguments.get("query"),
        )
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class CoreRuntimeToolProvider:
    def __init__(self, recall: RecallService) -> None:
        self.recall = recall

    async def snapshot(self, actor: ActorContext, session_id: UUID) -> tuple[RuntimeTool, ...]:
        return (cast(RuntimeTool, RecallRuntimeTool(self.recall, actor, session_id)),)
