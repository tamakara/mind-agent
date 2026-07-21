import asyncio
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from workhub.domain.knowledge import (
    KnowledgeDocument,
    KnowledgeNode,
    KnowledgeSearchResult,
)
from workhub.errors import ApplicationError
from workhub.knowledge.indexer import KnowledgeIndexer
from workhub.knowledge.repository import KnowledgeRepository
from workhub.knowledge.vector_store import KnowledgeVectorStore
from workhub.providers.openai import EmbeddingProvider
from workhub.storage import Database


class KnowledgeService:
    def __init__(
        self,
        database: Database,
        repository: KnowledgeRepository,
        indexer: KnowledgeIndexer,
        vector_store: KnowledgeVectorStore,
        embedding_factory: Any,
    ) -> None:
        self.database = database
        self.repository = repository
        self.indexer = indexer
        self.vector_store = vector_store
        self.embedding_factory = embedding_factory
        self._mutation_lock = asyncio.Lock()

    async def list_nodes(self) -> list[KnowledgeNode]:
        return await self.repository.list_nodes()

    async def list_directory(self, path: str | None = None) -> list[KnowledgeNode]:
        normalized = (path or "").strip("/")
        parsed = PurePosixPath(path or ".")
        if parsed.is_absolute() or ".." in parsed.parts or "\\" in (path or ""):
            raise ApplicationError("knowledge_path_invalid", "Knowledge path is invalid.")
        nodes = await self.repository.list_nodes()
        expected_parent = normalized or "."
        return [
            node
            for node in nodes
            if str(PurePosixPath(node.relative_path).parent) == expected_parent
        ]

    async def read_document(self, node_id: UUID) -> KnowledgeDocument:
        return await self.repository.read_document(node_id)

    async def read_lines(self, node_id: UUID, *, line_start: int, line_end: int) -> dict[str, Any]:
        if line_start < 1 or line_end < line_start or line_end - line_start + 1 > 500:
            raise ApplicationError(
                "knowledge_line_range_invalid", "Line range must contain 1 to 500 lines."
            )
        document = await self.repository.read_document(node_id)
        lines = document.content.splitlines()
        selected = lines[line_start - 1 : line_end]
        return {
            "document_id": str(node_id),
            "relative_path": document.relative_path,
            "line_start": line_start,
            "line_end": min(line_end, len(lines)),
            "content": "\n".join(selected),
        }

    async def create_directory(self, **kwargs: Any) -> KnowledgeNode:
        async with self._mutation_lock, self.indexer.mutation_guard():
            return await self.repository.create_directory(**kwargs)

    async def create_document(self, **kwargs: Any) -> KnowledgeNode:
        async with self._mutation_lock, self.indexer.mutation_guard():
            node = await self.repository.create_document(**kwargs)
        self.indexer.notify()
        return node

    async def save_document(self, node_id: UUID, **kwargs: Any) -> KnowledgeNode:
        async with self._mutation_lock, self.indexer.mutation_guard():
            node, changed = await self.repository.save_document(node_id, **kwargs)
        if changed:
            self.indexer.notify()
        return node

    async def move_node(self, node_id: UUID, **kwargs: Any) -> KnowledgeNode:
        async with self._mutation_lock, self.indexer.mutation_guard():
            return await self.repository.move_node(node_id, **kwargs)

    async def delete_node(self, node_id: UUID, **kwargs: Any) -> None:
        async with self._mutation_lock, self.indexer.mutation_guard():
            index_keys = await self.repository.delete_node(node_id, **kwargs)
        await asyncio.gather(
            *(self.vector_store.delete(index_key) for index_key in index_keys),
            return_exceptions=True,
        )

    async def retry(self, node_id: UUID) -> None:
        async with self._mutation_lock, self.indexer.mutation_guard():
            await self.repository.retry_job(node_id)
        self.indexer.notify()

    async def search(
        self, query: str, *, top_k: int = 5, score_threshold: float | None = None
    ) -> list[KnowledgeSearchResult]:
        if not query.strip():
            raise ApplicationError("knowledge_query_invalid", "Search query must not be empty.")
        provider: EmbeddingProvider = await self.embedding_factory()
        vectors = await provider.embed([query])
        if len(vectors) != 1 or not vectors[0]:
            raise ApplicationError("embedding_failed", "Embedding provider returned no vector.")
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT n.id AS document_id, n.relative_path, v.version, g.index_key
                    FROM knowledge_generations g
                    JOIN knowledge_document_versions v ON v.id = g.version_id
                    JOIN knowledge_nodes n ON n.id = v.document_node_id
                    WHERE g.status = 'active'
                    """
                )
            ).fetchall()
        results: list[KnowledgeSearchResult] = []
        for row in rows:
            try:
                matches = await self.vector_store.query(
                    str(row["index_key"]), vectors[0], limit=top_k
                )
            except Exception:
                continue
            for match in matches:
                metadata = match["metadata"]
                score = 1.0 / (1.0 + float(match["distance"]))
                if score_threshold is not None and score < score_threshold:
                    continue
                results.append(
                    KnowledgeSearchResult(
                        document_id=UUID(str(row["document_id"])),
                        version=int(row["version"]),
                        relative_path=str(row["relative_path"]),
                        heading_path=str(metadata.get("heading_path") or "") or None,
                        line_start=int(metadata["line_start"]),
                        line_end=int(metadata["line_end"]),
                        snippet=str(match["document"]),
                        score=score,
                    )
                )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]
