from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: UUID
    parent_id: UUID | None
    node_type: Literal["directory", "document"]
    name: str
    relative_path: str
    revision: int = Field(ge=0)
    reconciliation_required: bool
    created_at: str
    updated_at: str
    version: int | None = Field(default=None, ge=1)
    content_sha256: str | None = None
    index_status: Literal["queued", "indexing", "active", "failed"] | None = None
    index_error_code: str | None = None


class KnowledgeDocument(KnowledgeNode):
    content: str


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    heading_path: str | None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class KnowledgeSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: UUID
    version: int = Field(ge=1)
    relative_path: str
    heading_path: str | None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    snippet: str
    score: float
