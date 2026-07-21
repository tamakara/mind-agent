import hashlib
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from uuid import UUID

import aiosqlite

from workhub.audit import AuditEvent, AuditWriter
from workhub.domain import format_rfc3339, new_uuid4, utc_now
from workhub.domain.knowledge import KnowledgeDocument, KnowledgeNode
from workhub.errors import ApplicationError
from workhub.storage import Database
from workhub.storage.files import UnsafePathError, atomic_write_bytes, resolve_relative_path
from workhub.storage.layout import WorkHubDataLayout

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
NodeType = Literal["directory", "document"]


class KnowledgeRepository:
    def __init__(self, database: Database, layout: WorkHubDataLayout, audit: AuditWriter) -> None:
        self.database = database
        self.layout = layout
        self.audit = audit

    async def list_nodes(self) -> list[KnowledgeNode]:
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT n.*, v.version, v.content_sha256, v.status AS index_status,
                           j.error_code AS index_error_code
                    FROM knowledge_nodes n
                    LEFT JOIN knowledge_document_versions v
                      ON v.id = (
                        SELECT id FROM knowledge_document_versions
                        WHERE document_node_id = n.id ORDER BY version DESC LIMIT 1
                      )
                    LEFT JOIN knowledge_index_jobs j ON j.version_id = v.id
                    ORDER BY n.relative_path
                    """
                )
            ).fetchall()
        return [_node(row) for row in rows]

    async def get_node(self, node_id: UUID) -> KnowledgeNode:
        async with self.database.connect() as connection:
            row = await _node_row(connection, node_id)
        if row is None:
            raise ApplicationError(
                "knowledge_not_found", "Knowledge node was not found.", status_code=404
            )
        return _node(row)

    async def read_document(self, node_id: UUID) -> KnowledgeDocument:
        node = await self.get_node(node_id)
        if node.node_type != "document":
            raise ApplicationError("knowledge_not_document", "Knowledge node is not a document.")
        path = self._document_path(node.relative_path, must_exist=True)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ApplicationError(
                "knowledge_invalid_utf8", "Document is not valid UTF-8."
            ) from exc
        return KnowledgeDocument(**node.model_dump(), content=content)

    async def create_directory(
        self,
        *,
        parent_id: UUID | None,
        name: str,
        actor_id: str,
        request_id: str,
    ) -> KnowledgeNode:
        return await self._create_node(
            parent_id=parent_id,
            name=name,
            node_type="directory",
            content=None,
            actor_id=actor_id,
            request_id=request_id,
        )

    async def create_document(
        self,
        *,
        parent_id: UUID | None,
        name: str,
        content: str,
        actor_id: str,
        request_id: str,
    ) -> KnowledgeNode:
        return await self._create_node(
            parent_id=parent_id,
            name=name,
            node_type="document",
            content=content,
            actor_id=actor_id,
            request_id=request_id,
        )

    async def _create_node(
        self,
        *,
        parent_id: UUID | None,
        name: str,
        node_type: NodeType,
        content: str | None,
        actor_id: str,
        request_id: str,
    ) -> KnowledgeNode:
        _validate_name(name, node_type)
        parent_path = await self._parent_path(parent_id)
        relative_path = _join(parent_path, name)
        destination = self._document_path(relative_path)
        node_id = new_uuid4()
        version_id = new_uuid4() if node_type == "document" else None
        timestamp = format_rfc3339(utc_now())
        content_bytes = _content_bytes(content or "") if node_type == "document" else None
        staging = self._staging_path(version_id) if version_id else None
        try:
            if node_type == "directory":
                destination.mkdir(parents=False, exist_ok=False)
            else:
                assert content_bytes is not None and staging is not None
                atomic_write_bytes(staging, content_bytes)
                atomic_write_bytes(destination, content_bytes)
            async with self.database.transaction(write=True) as connection:
                await connection.execute(
                    """
                    INSERT INTO knowledge_nodes(
                        id, parent_id, node_type, name, relative_path, revision,
                        reconciliation_required, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        str(node_id),
                        str(parent_id) if parent_id else None,
                        node_type,
                        name,
                        relative_path,
                        timestamp,
                        timestamp,
                    ),
                )
                if version_id is not None and content_bytes is not None:
                    await _insert_version_job(
                        connection,
                        version_id=version_id,
                        node_id=node_id,
                        version=1,
                        content_bytes=content_bytes,
                        timestamp=timestamp,
                    )
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="knowledge.created",
                        actor_type="admin",
                        actor_id=actor_id,
                        subject_type="knowledge_node",
                        subject_id=str(node_id),
                        request_id=request_id,
                        summary={"node_type": node_type, "relative_path": relative_path},
                    ),
                )
        except (OSError, aiosqlite.IntegrityError) as exc:
            if node_type == "directory":
                destination.rmdir() if destination.is_dir() else None
            else:
                destination.unlink(missing_ok=True)
                if staging is not None:
                    staging.unlink(missing_ok=True)
            if isinstance(exc, aiosqlite.IntegrityError):
                raise ApplicationError(
                    "knowledge_name_conflict",
                    "A node with this name already exists.",
                    status_code=409,
                ) from exc
            raise ApplicationError(
                "knowledge_file_error", "Knowledge file operation failed."
            ) from exc
        return await self.get_node(node_id)

    async def save_document(
        self,
        node_id: UUID,
        *,
        content: str,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> tuple[KnowledgeNode, bool]:
        node = await self.get_node(node_id)
        if node.node_type != "document":
            raise ApplicationError("knowledge_not_document", "Knowledge node is not a document.")
        if node.revision != expected_revision:
            raise ApplicationError(
                "revision_conflict", "Resource revision is stale.", status_code=409
            )
        content_bytes = _content_bytes(content)
        digest = hashlib.sha256(content_bytes).hexdigest()
        if digest == node.content_sha256:
            return node, False
        original = self._document_path(node.relative_path, must_exist=True)
        previous = original.read_bytes()
        version_id = new_uuid4()
        staging = self._staging_path(version_id)
        timestamp = format_rfc3339(utc_now())
        atomic_write_bytes(staging, content_bytes)
        atomic_write_bytes(original, content_bytes)
        try:
            async with self.database.transaction(write=True) as connection:
                current = await _node_row(connection, node_id)
                if current is None:
                    raise ApplicationError(
                        "knowledge_not_found", "Knowledge node was not found.", status_code=404
                    )
                if int(current["revision"]) != expected_revision:
                    raise ApplicationError(
                        "revision_conflict", "Resource revision is stale.", status_code=409
                    )
                next_version = int(current["version"] or 0) + 1
                await connection.execute(
                    """
                    UPDATE knowledge_nodes SET revision = revision + 1, updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (timestamp, str(node_id), expected_revision),
                )
                await _insert_version_job(
                    connection,
                    version_id=version_id,
                    node_id=node_id,
                    version=next_version,
                    content_bytes=content_bytes,
                    timestamp=timestamp,
                )
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="knowledge.saved",
                        actor_type="admin",
                        actor_id=actor_id,
                        subject_type="knowledge_node",
                        subject_id=str(node_id),
                        request_id=request_id,
                        summary={"content_sha256": digest, "size_bytes": len(content_bytes)},
                    ),
                )
        except BaseException:
            atomic_write_bytes(original, previous)
            staging.unlink(missing_ok=True)
            raise
        return await self.get_node(node_id), True

    async def move_node(
        self,
        node_id: UUID,
        *,
        parent_id: UUID | None,
        name: str,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> KnowledgeNode:
        node = await self.get_node(node_id)
        _validate_name(name, node.node_type)
        if node.revision != expected_revision:
            raise ApplicationError(
                "revision_conflict", "Resource revision is stale.", status_code=409
            )
        parent_path = await self._parent_path(parent_id, moving_node_id=node_id)
        new_relative = _join(parent_path, name)
        if new_relative == node.relative_path:
            return node
        source = self._document_path(node.relative_path, must_exist=True)
        destination = self._document_path(new_relative)
        if destination.exists():
            raise ApplicationError(
                "knowledge_name_conflict", "Destination already exists.", status_code=409
            )
        if not destination.parent.is_dir() or destination.parent.is_symlink():
            raise ApplicationError(
                "knowledge_parent_missing", "Destination directory is unavailable."
            )
        os.replace(source, destination)
        timestamp = format_rfc3339(utc_now())
        try:
            async with self.database.transaction(write=True) as connection:
                cursor = await connection.execute(
                    """
                    UPDATE knowledge_nodes
                    SET parent_id = ?, name = ?, relative_path = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        str(parent_id) if parent_id else None,
                        name,
                        new_relative,
                        timestamp,
                        str(node_id),
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ApplicationError(
                        "revision_conflict", "Resource revision is stale.", status_code=409
                    )
                if node.node_type == "directory":
                    old_prefix = f"{node.relative_path}/"
                    escaped_prefix = _like_escape(old_prefix)
                    rows = await (
                        await connection.execute(
                            """
                            SELECT id, relative_path FROM knowledge_nodes
                            WHERE relative_path LIKE ? ESCAPE '!'
                            """,
                            (f"{escaped_prefix}%",),
                        )
                    ).fetchall()
                    for child in rows:
                        child_relative = str(child["relative_path"])
                        replacement = f"{new_relative}/{child_relative[len(old_prefix) :]}"
                        await connection.execute(
                            """
                            UPDATE knowledge_nodes
                            SET relative_path = ?, revision = revision + 1, updated_at = ?
                            WHERE id = ?
                            """,
                            (replacement, timestamp, child["id"]),
                        )
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="knowledge.moved",
                        actor_type="admin",
                        actor_id=actor_id,
                        subject_type="knowledge_node",
                        subject_id=str(node_id),
                        request_id=request_id,
                        summary={"from": node.relative_path, "to": new_relative},
                    ),
                )
        except BaseException:
            os.replace(destination, source)
            raise
        return await self.get_node(node_id)

    async def delete_node(
        self,
        node_id: UUID,
        *,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> list[str]:
        node = await self.get_node(node_id)
        if node.revision != expected_revision:
            raise ApplicationError(
                "revision_conflict", "Resource revision is stale.", status_code=409
            )
        source = self._document_path(node.relative_path, must_exist=True)
        tombstone = self.layout.knowledge_staging / f"delete-{new_uuid4().hex}"
        os.replace(source, tombstone)
        index_keys: list[str] = []
        try:
            async with self.database.transaction(write=True) as connection:
                rows = await (
                    await connection.execute(
                        """
                        SELECT DISTINCT n.id, n.relative_path
                        FROM knowledge_nodes n
                        WHERE n.id = ? OR n.relative_path LIKE ? ESCAPE '!'
                        ORDER BY LENGTH(n.relative_path) DESC
                        """,
                        (str(node_id), f"{_like_escape(node.relative_path + '/')}%"),
                    )
                ).fetchall()
                generation_rows = await (
                    await connection.execute(
                        """
                        SELECT g.index_key
                        FROM knowledge_generations g
                        JOIN knowledge_document_versions v ON v.id = g.version_id
                        JOIN knowledge_nodes n ON n.id = v.document_node_id
                        WHERE n.id = ? OR n.relative_path LIKE ? ESCAPE '!'
                        """,
                        (str(node_id), f"{_like_escape(node.relative_path + '/')}%"),
                    )
                ).fetchall()
                index_keys = [str(row["index_key"]) for row in generation_rows]
                descendant_ids = [str(row["id"]) for row in rows]
                for descendant_id in descendant_ids:
                    await connection.execute(
                        "DELETE FROM knowledge_nodes WHERE id = ?", (descendant_id,)
                    )
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="knowledge.deleted",
                        actor_type="admin",
                        actor_id=actor_id,
                        subject_type="knowledge_node",
                        subject_id=str(node_id),
                        request_id=request_id,
                        summary={"relative_path": node.relative_path},
                    ),
                )
        except BaseException:
            os.replace(tombstone, source)
            raise
        if tombstone.is_dir():
            shutil.rmtree(tombstone)
        else:
            tombstone.unlink(missing_ok=True)
        return index_keys

    async def retry_job(self, node_id: UUID) -> None:
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                """
                UPDATE knowledge_index_jobs SET status = 'queued', error_code = NULL,
                    error_json = NULL, started_at = NULL, completed_at = NULL
                WHERE version_id = (
                    SELECT id FROM knowledge_document_versions
                    WHERE document_node_id = ? ORDER BY version DESC LIMIT 1
                ) AND status = 'failed'
                """,
                (str(node_id),),
            )
            if cursor.rowcount != 1:
                raise ApplicationError(
                    "knowledge_job_not_failed", "No failed index job is available.", status_code=409
                )
            await connection.execute(
                """
                UPDATE knowledge_document_versions SET status = 'queued'
                WHERE id = (
                    SELECT id FROM knowledge_document_versions
                    WHERE document_node_id = ? ORDER BY version DESC LIMIT 1
                )
                """,
                (str(node_id),),
            )

    async def _parent_path(
        self, parent_id: UUID | None, *, moving_node_id: UUID | None = None
    ) -> str:
        if parent_id is None:
            return ""
        parent = await self.get_node(parent_id)
        if parent.node_type != "directory":
            raise ApplicationError("knowledge_parent_invalid", "Parent must be a directory.")
        if moving_node_id is not None:
            moving = await self.get_node(moving_node_id)
            if parent.relative_path == moving.relative_path or parent.relative_path.startswith(
                f"{moving.relative_path}/"
            ):
                raise ApplicationError("knowledge_cycle", "A directory cannot move into itself.")
        return parent.relative_path

    def _document_path(self, relative_path: str, *, must_exist: bool = False) -> Path:
        try:
            return resolve_relative_path(
                self.layout.knowledge_originals, relative_path, must_exist=must_exist
            )
        except UnsafePathError as exc:
            raise ApplicationError("knowledge_path_invalid", "Knowledge path is invalid.") from exc

    def _staging_path(self, version_id: UUID) -> Path:
        return resolve_relative_path(
            self.layout.knowledge_staging, f"{version_id}.source", must_exist=False
        )


async def _node_row(connection: aiosqlite.Connection, node_id: UUID) -> aiosqlite.Row | None:
    return await (
        await connection.execute(
            """
            SELECT n.*, v.version, v.content_sha256, v.status AS index_status,
                   j.error_code AS index_error_code
            FROM knowledge_nodes n
            LEFT JOIN knowledge_document_versions v
              ON v.id = (
                SELECT id FROM knowledge_document_versions
                WHERE document_node_id = n.id ORDER BY version DESC LIMIT 1
              )
            LEFT JOIN knowledge_index_jobs j ON j.version_id = v.id
            WHERE n.id = ?
            """,
            (str(node_id),),
        )
    ).fetchone()


async def _insert_version_job(
    connection: aiosqlite.Connection,
    *,
    version_id: UUID,
    node_id: UUID,
    version: int,
    content_bytes: bytes,
    timestamp: str,
) -> None:
    await connection.execute(
        """
        INSERT INTO knowledge_document_versions(
            id, document_node_id, version, content_sha256, size_bytes, status, created_at
        ) VALUES (?, ?, ?, ?, ?, 'queued', ?)
        """,
        (
            str(version_id),
            str(node_id),
            version,
            hashlib.sha256(content_bytes).hexdigest(),
            len(content_bytes),
            timestamp,
        ),
    )
    await connection.execute(
        """
        INSERT INTO knowledge_index_jobs(id, version_id, status, attempts, created_at)
        VALUES (?, ?, 'queued', 0, ?)
        """,
        (str(new_uuid4()), str(version_id), timestamp),
    )


def _node(row: aiosqlite.Row) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=UUID(str(row["id"])),
        parent_id=UUID(str(row["parent_id"])) if row["parent_id"] else None,
        node_type=cast(NodeType, str(row["node_type"])),
        name=str(row["name"]),
        relative_path=str(row["relative_path"]),
        revision=int(row["revision"]),
        reconciliation_required=bool(row["reconciliation_required"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        version=int(row["version"]) if row["version"] is not None else None,
        content_sha256=str(row["content_sha256"]) if row["content_sha256"] else None,
        index_status=cast(Literal["queued", "indexing", "active", "failed"], row["index_status"])
        if row["index_status"]
        else None,
        index_error_code=str(row["index_error_code"]) if row["index_error_code"] else None,
    )


def _validate_name(name: str, node_type: NodeType) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise ApplicationError("knowledge_name_invalid", "Knowledge name is invalid.")
    if len(name) > 255:
        raise ApplicationError("knowledge_name_invalid", "Knowledge name is too long.")
    if node_type == "document" and PurePosixPath(name).suffix.lower() not in {".md", ".txt"}:
        raise ApplicationError(
            "knowledge_type_invalid", "Only .md and .txt documents are supported."
        )


def _join(parent: str, name: str) -> str:
    return str(PurePosixPath(parent) / name) if parent else name


def _like_escape(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _content_bytes(content: str) -> bytes:
    try:
        value = content.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ApplicationError("knowledge_invalid_utf8", "Document must be valid UTF-8.") from exc
    if len(value) > MAX_DOCUMENT_BYTES:
        raise ApplicationError(
            "knowledge_too_large", "Document exceeds the 10 MiB limit.", status_code=413
        )
    return value
