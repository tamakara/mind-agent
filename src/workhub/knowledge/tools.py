import json
from dataclasses import dataclass
from typing import Any, ClassVar, cast
from uuid import UUID

from workhub.domain import ActorContext, ToolDefinition
from workhub.knowledge.service import KnowledgeService
from workhub.runtime.tools import RuntimeTool


@dataclass(frozen=True, slots=True)
class SearchKnowledgeTool:
    service: KnowledgeService
    definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="search_knowledge",
        description="Search indexed company knowledge and return source locations.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "score_threshold": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    async def execute(self, arguments: dict[str, Any]) -> str:
        results = await self.service.search(
            str(arguments["query"]),
            top_k=int(arguments.get("top_k", 5)),
            score_threshold=(
                float(arguments["score_threshold"])
                if arguments.get("score_threshold") is not None
                else None
            ),
        )
        return json.dumps(
            [item.model_dump(mode="json") for item in results],
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class ListKnowledgeDirectoryTool:
    service: KnowledgeService
    definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="list_knowledge_directory",
        description="List direct children of a shared knowledge directory.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
    )

    async def execute(self, arguments: dict[str, Any]) -> str:
        nodes = await self.service.list_directory(arguments.get("path"))
        return json.dumps(
            [
                {
                    "document_id": str(node.node_id) if node.node_type == "document" else None,
                    "node_type": node.node_type,
                    "name": node.name,
                    "relative_path": node.relative_path,
                }
                for node in nodes
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class ReadKnowledgeDocumentTool:
    service: KnowledgeService
    definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="read_knowledge_document",
        description="Read up to 500 source lines from a shared knowledge document.",
        input_schema={
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "format": "uuid"},
                "line_start": {"type": "integer", "minimum": 1},
                "line_end": {"type": "integer", "minimum": 1},
            },
            "required": ["document_id", "line_start", "line_end"],
            "additionalProperties": False,
        },
    )

    async def execute(self, arguments: dict[str, Any]) -> str:
        result = await self.service.read_lines(
            UUID(str(arguments["document_id"])),
            line_start=int(arguments["line_start"]),
            line_end=int(arguments["line_end"]),
        )
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class KnowledgeRuntimeToolProvider:
    def __init__(self, service: KnowledgeService) -> None:
        self.service = service

    async def snapshot(self, actor: ActorContext, session_id: UUID) -> tuple[RuntimeTool, ...]:
        del actor, session_id
        return (
            cast(RuntimeTool, SearchKnowledgeTool(self.service)),
            cast(RuntimeTool, ListKnowledgeDirectoryTool(self.service)),
            cast(RuntimeTool, ReadKnowledgeDocumentTool(self.service)),
        )
