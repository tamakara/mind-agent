import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from workhub.audit import AuditWriter
from workhub.errors import ApplicationError
from workhub.knowledge import KnowledgeIndexer, KnowledgeReconciler, KnowledgeRepository
from workhub.knowledge.service import KnowledgeService
from workhub.knowledge.vector_store import KnowledgeVectorStore
from workhub.storage import Database
from workhub.storage.layout import WorkHubDataLayout


class _Embeddings:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.fail:
            raise RuntimeError("embedding offline")
        return [_vector(text) for text in texts]


def _vector(text: str) -> list[float]:
    normalized = text.lower()
    return [
        float(normalized.count("leave") + 1),
        float(normalized.count("security") + 1),
        float(len(normalized) % 17 + 1),
    ]


async def _services(
    tmp_path: Path,
) -> tuple[
    Database,
    WorkHubDataLayout,
    AuditWriter,
    KnowledgeRepository,
    KnowledgeIndexer,
    KnowledgeService,
    KnowledgeVectorStore,
    _Embeddings,
]:
    root = tmp_path / "data"
    layout = WorkHubDataLayout(
        root=root,
        database=root / "app.db",
        knowledge_originals=root / "knowledge" / "originals",
        knowledge_staging=root / "knowledge" / "staging",
        knowledge_index=root / "knowledge" / "index",
        logs=root / "logs",
    )
    for path in (
        layout.root,
        layout.knowledge_originals,
        layout.knowledge_staging,
        layout.knowledge_index,
        layout.logs,
    ):
        path.mkdir(parents=True, exist_ok=True)
    database = Database(layout.database)
    await database.migrate()
    audit = AuditWriter(database)
    repository = KnowledgeRepository(database, layout, audit)
    embeddings = _Embeddings()

    async def factory() -> _Embeddings:
        return embeddings

    vector_store = KnowledgeVectorStore(layout.knowledge_index)
    indexer = KnowledgeIndexer(database, layout, vector_store, factory)
    service = KnowledgeService(database, repository, indexer, vector_store, factory)
    return (
        database,
        layout,
        audit,
        repository,
        indexer,
        service,
        vector_store,
        embeddings,
    )


async def _create_indexed_document(
    service: KnowledgeService, indexer: KnowledgeIndexer
) -> tuple[Any, Any]:
    directory = await service.create_directory(
        parent_id=None,
        name="policies",
        actor_id="admin",
        request_id="directory",
    )
    document = await service.create_document(
        parent_id=directory.node_id,
        name="leave.md",
        content="# Leave Policy\nEmployees receive annual leave.\n",
        actor_id="admin",
        request_id="document",
    )
    assert await indexer.process_next() is True
    return directory, await service.read_document(document.node_id)


async def test_knowledge_crud_hash_revision_generation_and_rename(
    tmp_path: Path,
) -> None:
    _, layout, _, _, indexer, service, vector_store, embeddings = await _services(tmp_path)
    directory, document = await _create_indexed_document(service, indexer)

    assert document.index_status == "active"
    assert document.version == 1
    assert document.content_sha256 == hashlib.sha256(document.content.encode()).hexdigest()
    assert len(embeddings.calls) == 1
    assert [node.relative_path for node in await service.list_directory()] == ["policies"]
    assert [node.relative_path for node in await service.list_directory("policies")] == [
        "policies/leave.md"
    ]

    unchanged = await service.save_document(
        document.node_id,
        content=document.content,
        expected_revision=document.revision,
        actor_id="admin",
        request_id="unchanged",
    )
    assert unchanged.revision == document.revision
    assert await indexer.process_next() is False
    assert len(embeddings.calls) == 1

    renamed = await service.move_node(
        document.node_id,
        parent_id=directory.node_id,
        name="annual-leave.md",
        expected_revision=document.revision,
        actor_id="admin",
        request_id="rename",
    )
    assert renamed.relative_path == "policies/annual-leave.md"
    assert not (layout.knowledge_originals / "policies" / "leave.md").exists()
    assert len(embeddings.calls) == 1

    changed = await service.save_document(
        document.node_id,
        content="# Leave Policy\nSecurity review and annual leave.\n",
        expected_revision=renamed.revision,
        actor_id="admin",
        request_id="changed",
    )
    assert changed.version == 2 and changed.index_status == "queued"
    assert await indexer.process_next() is True
    assert len(embeddings.calls) == 2

    results = await service.search("security", top_k=3)
    assert results
    assert results[0].document_id == document.node_id
    assert results[0].version == 2
    assert results[0].relative_path == "policies/annual-leave.md"
    assert results[0].line_start == 1
    assert results[0].heading_path == "Leave Policy"

    async with service.database.connect() as connection:
        keys = await (
            await connection.execute(
                """
                SELECT index_key FROM knowledge_generations g
                JOIN knowledge_document_versions v ON v.id = g.version_id
                WHERE v.document_node_id = ?
                """,
                (str(document.node_id),),
            )
        ).fetchall()
    await service.delete_node(
        directory.node_id,
        expected_revision=directory.revision,
        actor_id="admin",
        request_id="delete",
    )
    assert await service.list_nodes() == []
    for row in keys:
        assert not await vector_store.exists(str(row["index_key"]))


async def test_failed_generation_keeps_old_active_and_retry_succeeds(
    tmp_path: Path,
) -> None:
    database, _, _, _, indexer, service, _, embeddings = await _services(tmp_path)
    _, document = await _create_indexed_document(service, indexer)
    changed = await service.save_document(
        document.node_id,
        content="# Leave Policy\nChanged content.\n",
        expected_revision=document.revision,
        actor_id="admin",
        request_id="save",
    )
    embeddings.fail = True
    assert await indexer.process_next() is True
    failed = await service.read_document(document.node_id)
    assert failed.index_status == "failed"
    assert failed.index_error_code == "index_failed"
    async with database.connect() as connection:
        active = await (
            await connection.execute(
                """
                SELECT COUNT(*) FROM knowledge_generations g
                JOIN knowledge_document_versions v ON v.id = g.version_id
                WHERE v.document_node_id = ? AND g.status = 'active'
                """,
                (str(document.node_id),),
            )
        ).fetchone()
        failed_job = await (
            await connection.execute(
                """
                SELECT error_json FROM knowledge_index_jobs
                WHERE version_id = (
                    SELECT id FROM knowledge_document_versions
                    WHERE document_node_id = ? ORDER BY version DESC LIMIT 1
                )
                """,
                (str(document.node_id),),
            )
        ).fetchone()
    assert active is not None and active[0] == 1
    assert failed_job is not None
    assert json.loads(str(failed_job["error_json"])) == {"type": "RuntimeError"}

    embeddings.fail = False
    await service.retry(document.node_id)
    assert await indexer.process_next() is True
    recovered = await service.read_document(document.node_id)
    assert recovered.index_status == "active"
    assert recovered.version == changed.version


async def test_paths_limits_stale_revision_and_reconciler(tmp_path: Path) -> None:
    database, layout, audit, repository, indexer, service, vector_store, _ = await _services(
        tmp_path
    )
    _, document = await _create_indexed_document(service, indexer)

    with pytest.raises(ApplicationError, match=r"Only \.md and \.txt"):
        await service.create_document(
            parent_id=None,
            name="unsafe.pdf",
            content="bad",
            actor_id="admin",
            request_id="bad-type",
        )
    with pytest.raises(ApplicationError, match="invalid"):
        await service.create_directory(
            parent_id=None,
            name="../escape",
            actor_id="admin",
            request_id="bad-path",
        )
    with pytest.raises(ApplicationError, match="10 MiB"):
        await service.save_document(
            document.node_id,
            content="x" * (10 * 1024 * 1024 + 1),
            expected_revision=document.revision,
            actor_id="admin",
            request_id="large",
        )
    with pytest.raises(ApplicationError, match="stale"):
        await service.save_document(
            document.node_id,
            content="new",
            expected_revision=999,
            actor_id="admin",
            request_id="stale",
        )

    original = layout.knowledge_originals / document.relative_path
    original.write_text("# Externally changed\nNew text\n", encoding="utf-8")
    reconciler = KnowledgeReconciler(database, layout, repository, indexer, vector_store, audit)
    issues = await reconciler.run_once()
    assert {item["issue"] for item in issues} == {"content_hash_mismatch"}
    reconciled = await service.read_document(document.node_id)
    assert reconciled.reconciliation_required is True
    assert reconciled.version == 2
    assert reconciled.index_status == "queued"

    await vector_store.write_generation("gen_orphan", [], [])
    issues = await reconciler.run_once()
    assert {item["issue"] for item in issues} >= {"orphan_index"}
    assert not await vector_store.exists("gen_orphan")


async def test_directory_move_updates_descendant_paths_and_revisions(tmp_path: Path) -> None:
    _, _, _, _, indexer, service, _, embeddings = await _services(tmp_path)
    directory, document = await _create_indexed_document(service, indexer)

    moved = await service.move_node(
        directory.node_id,
        parent_id=None,
        name="handbook",
        expected_revision=directory.revision,
        actor_id="admin",
        request_id="move-directory",
    )
    descendant = await service.read_document(document.node_id)

    assert moved.relative_path == "handbook"
    assert moved.revision == directory.revision + 1
    assert descendant.relative_path == "handbook/leave.md"
    assert descendant.revision == document.revision + 1
    assert len(embeddings.calls) == 1
    assert await indexer.process_next() is False
