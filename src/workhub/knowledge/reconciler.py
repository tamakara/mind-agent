import hashlib

from workhub.audit import AuditEvent, AuditWriter
from workhub.knowledge.indexer import KnowledgeIndexer
from workhub.knowledge.repository import KnowledgeRepository
from workhub.knowledge.vector_store import KnowledgeVectorStore
from workhub.storage import Database
from workhub.storage.layout import WorkHubDataLayout


class KnowledgeReconciler:
    def __init__(
        self,
        database: Database,
        layout: WorkHubDataLayout,
        repository: KnowledgeRepository,
        indexer: KnowledgeIndexer,
        vector_store: KnowledgeVectorStore,
        audit: AuditWriter,
    ) -> None:
        self.database = database
        self.layout = layout
        self.repository = repository
        self.indexer = indexer
        self.vector_store = vector_store
        self.audit = audit

    async def run_once(self) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        nodes = await self.repository.list_nodes()
        expected = {node.relative_path for node in nodes}
        for node in nodes:
            path = self.layout.knowledge_originals / node.relative_path
            issue: str | None = None
            if path.is_symlink() or not path.exists():
                issue = "missing_original"
            elif node.node_type == "document":
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if node.content_sha256 and digest != node.content_sha256:
                    issue = "content_hash_mismatch"
                    content = path.read_text(encoding="utf-8")
                    await self.repository.save_document(
                        node.node_id,
                        content=content,
                        expected_revision=node.revision,
                        actor_id="reconciler",
                        request_id="reconciler",
                    )
                    self.indexer.notify()
                elif node.index_status == "active":
                    index_key = await self._active_index_key(node.node_id)
                    if index_key and not await self.vector_store.exists(index_key):
                        issue = "active_index_missing"
                        await self._queue_existing_version(node.node_id)
                        self.indexer.notify()
            await self._set_flag(node.node_id, issue is not None)
            if issue:
                issues.append({"path": node.relative_path, "issue": issue})
        for path in self.layout.knowledge_originals.rglob("*"):
            if path.is_file() and not path.is_symlink():
                relative = path.relative_to(self.layout.knowledge_originals).as_posix()
                if relative not in expected:
                    issues.append({"path": relative, "issue": "orphan_original"})
        database_keys = await self._database_index_keys()
        for orphan_key in await self.vector_store.list_keys() - database_keys:
            await self.vector_store.delete(orphan_key)
            issues.append({"path": orphan_key, "issue": "orphan_index"})
        for recorded_issue in issues:
            await self.audit.write(
                AuditEvent(
                    event_type="knowledge.reconciliation_issue",
                    subject_type="knowledge_path",
                    subject_id=recorded_issue["path"],
                    summary={"issue": recorded_issue["issue"]},
                    error_code=recorded_issue["issue"],
                )
            )
        return issues

    async def _database_index_keys(self) -> set[str]:
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute("SELECT index_key FROM knowledge_generations")
            ).fetchall()
        return {str(row["index_key"]) for row in rows}

    async def _set_flag(self, node_id: object, required: bool) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                "UPDATE knowledge_nodes SET reconciliation_required = ? WHERE id = ?",
                (int(required), str(node_id)),
            )

    async def _active_index_key(self, node_id: object) -> str | None:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT g.index_key FROM knowledge_generations g
                    JOIN knowledge_document_versions v ON v.id = g.version_id
                    WHERE v.document_node_id = ? AND g.status = 'active'
                    ORDER BY g.activated_at DESC LIMIT 1
                    """,
                    (str(node_id),),
                )
            ).fetchone()
        return str(row["index_key"]) if row else None

    async def _queue_existing_version(self, node_id: object) -> None:
        async with self.database.transaction(write=True) as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT v.id, j.id AS job_id FROM knowledge_document_versions v
                    JOIN knowledge_index_jobs j ON j.version_id = v.id
                    WHERE v.document_node_id = ? ORDER BY v.version DESC LIMIT 1
                    """,
                    (str(node_id),),
                )
            ).fetchone()
            if row:
                await connection.execute(
                    "UPDATE knowledge_document_versions SET status = 'queued' WHERE id = ?",
                    (row["id"],),
                )
                await connection.execute(
                    """
                    UPDATE knowledge_index_jobs SET status = 'queued', error_code = NULL,
                        error_json = NULL, started_at = NULL, completed_at = NULL
                    WHERE id = ?
                    """,
                    (row["job_id"],),
                )
