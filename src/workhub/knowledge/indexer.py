import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from workhub.domain import format_rfc3339, new_uuid4, utc_now
from workhub.domain.knowledge import KnowledgeChunk
from workhub.knowledge.chunking import split_document
from workhub.knowledge.vector_store import KnowledgeVectorStore
from workhub.providers.openai import EmbeddingProvider
from workhub.storage import Database
from workhub.storage.layout import WorkHubDataLayout

logger = logging.getLogger(__name__)
EmbeddingFactory = Callable[[], Awaitable[EmbeddingProvider]]


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: UUID
    version_id: UUID
    document_id: UUID
    version: int
    relative_path: str
    content_sha256: str
    generation_id: UUID
    index_key: str


class KnowledgeIndexer:
    def __init__(
        self,
        database: Database,
        layout: WorkHubDataLayout,
        vector_store: KnowledgeVectorStore,
        embedding_factory: EmbeddingFactory,
    ) -> None:
        self.database = database
        self.layout = layout
        self.vector_store = vector_store
        self.embedding_factory = embedding_factory
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._run_lock = asyncio.Lock()

    async def start(self) -> None:
        await self.recover_interrupted()
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="knowledge-indexer")
        self.notify()

    async def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task
        self._task = None

    def notify(self) -> None:
        self._wake.set()

    @asynccontextmanager
    async def mutation_guard(self) -> AsyncIterator[None]:
        async with self._run_lock:
            yield

    async def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            while not self._stop.is_set() and await self.process_next():
                pass
            if self._stop.is_set():
                break
            await self._wake.wait()

    async def process_next(self) -> bool:
        async with self._run_lock:
            job = await self._claim()
            if job is None:
                return False
            try:
                content = self._read_snapshot(job)
                chunks = split_document(job.relative_path, content)
                provider = await self.embedding_factory()
                embeddings = await provider.embed([chunk.content for chunk in chunks])
                await self.vector_store.write_generation(job.index_key, chunks, embeddings)
                await self._activate(job, chunks)
                self._snapshot_path(job.version_id).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Knowledge indexing failed: %s", type(exc).__name__)
                try:
                    await self.vector_store.delete(job.index_key)
                except Exception:
                    logger.exception("Failed to remove incomplete knowledge generation")
                await self._fail(job, exc)
            return True

    async def recover_interrupted(self) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                """
                UPDATE knowledge_generations SET status = 'failed'
                WHERE status = 'pending'
                """
            )
            await connection.execute(
                """
                UPDATE knowledge_index_jobs
                SET status = 'queued', started_at = NULL, error_code = 'interrupted',
                    error_json = '{"retryable":true}'
                WHERE status = 'indexing'
                """
            )
            await connection.execute(
                """
                UPDATE knowledge_document_versions SET status = 'queued'
                WHERE status = 'indexing'
                """
            )

    async def _claim(self) -> ClaimedJob | None:
        timestamp = format_rfc3339(utc_now())
        generation_id = new_uuid4()
        index_key = f"gen_{generation_id.hex}"
        async with self.database.transaction(write=True) as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT j.id AS job_id, v.id AS version_id, v.document_node_id,
                           v.version, v.content_sha256, n.relative_path
                    FROM knowledge_index_jobs j
                    JOIN knowledge_document_versions v ON v.id = j.version_id
                    JOIN knowledge_nodes n ON n.id = v.document_node_id
                    WHERE j.status = 'queued'
                    ORDER BY j.created_at LIMIT 1
                    """
                )
            ).fetchone()
            if row is None:
                return None
            cursor = await connection.execute(
                """
                UPDATE knowledge_index_jobs
                SET status = 'indexing', attempts = attempts + 1, started_at = ?,
                    completed_at = NULL
                WHERE id = ? AND status = 'queued'
                """,
                (timestamp, row["job_id"]),
            )
            if cursor.rowcount != 1:
                return None
            await connection.execute(
                "UPDATE knowledge_document_versions SET status = 'indexing' WHERE id = ?",
                (row["version_id"],),
            )
            await connection.execute(
                """
                INSERT INTO knowledge_generations(
                    id, version_id, status, index_key, created_at
                ) VALUES (?, ?, 'pending', ?, ?)
                """,
                (str(generation_id), row["version_id"], index_key, timestamp),
            )
        return ClaimedJob(
            job_id=UUID(str(row["job_id"])),
            version_id=UUID(str(row["version_id"])),
            document_id=UUID(str(row["document_node_id"])),
            version=int(row["version"]),
            relative_path=str(row["relative_path"]),
            content_sha256=str(row["content_sha256"]),
            generation_id=generation_id,
            index_key=index_key,
        )

    def _read_snapshot(self, job: ClaimedJob) -> str:
        snapshot = self._snapshot_path(job.version_id)
        if snapshot.is_file():
            raw = snapshot.read_bytes()
        else:
            raw = (self.layout.knowledge_originals / job.relative_path).read_bytes()
        import hashlib

        if hashlib.sha256(raw).hexdigest() != job.content_sha256:
            raise RuntimeError("Knowledge snapshot hash mismatch")
        return raw.decode("utf-8", errors="strict")

    async def _activate(self, job: ClaimedJob, chunks: list[KnowledgeChunk]) -> None:
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            for chunk_no, chunk in enumerate(chunks):
                await connection.execute(
                    """
                    INSERT INTO knowledge_chunks(
                        id, generation_id, chunk_no, heading_path,
                        line_start, line_end, content
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(new_uuid4()),
                        str(job.generation_id),
                        chunk_no,
                        chunk.heading_path,
                        chunk.line_start,
                        chunk.line_end,
                        chunk.content,
                    ),
                )
            await connection.execute(
                """
                UPDATE knowledge_generations SET status = 'retired'
                WHERE status = 'active' AND version_id IN (
                    SELECT id FROM knowledge_document_versions WHERE document_node_id = ?
                )
                """,
                (str(job.document_id),),
            )
            await connection.execute(
                """
                UPDATE knowledge_generations
                SET status = 'active', activated_at = ? WHERE id = ? AND status = 'pending'
                """,
                (timestamp, str(job.generation_id)),
            )
            await connection.execute(
                "UPDATE knowledge_document_versions SET status = 'active' WHERE id = ?",
                (str(job.version_id),),
            )
            await connection.execute(
                """
                UPDATE knowledge_index_jobs
                SET status = 'succeeded', completed_at = ?, error_code = NULL, error_json = NULL
                WHERE id = ?
                """,
                (timestamp, str(job.job_id)),
            )
            await connection.execute(
                "UPDATE knowledge_nodes SET reconciliation_required = 0 WHERE id = ?",
                (str(job.document_id),),
            )

    async def _fail(self, job: ClaimedJob, exc: Exception) -> None:
        timestamp = format_rfc3339(utc_now())
        error_code = "embedding_failed" if "embed" in type(exc).__name__.lower() else "index_failed"
        error_json = json.dumps(
            {"type": type(exc).__name__},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                "UPDATE knowledge_generations SET status = 'failed' WHERE id = ?",
                (str(job.generation_id),),
            )
            await connection.execute(
                "UPDATE knowledge_document_versions SET status = 'failed' WHERE id = ?",
                (str(job.version_id),),
            )
            await connection.execute(
                """
                UPDATE knowledge_index_jobs
                SET status = 'failed', error_code = ?, error_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (error_code, error_json, timestamp, str(job.job_id)),
            )

    def _snapshot_path(self, version_id: UUID) -> Path:
        return self.layout.knowledge_staging / f"{version_id}.source"
