import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, cast
from uuid import UUID

import aiosqlite

from workhub.audit import AuditEvent, AuditWriter, redact_audit_value
from workhub.domain import (
    ActorContext,
    ChannelAddress,
    PendingAction,
    PendingActionStatus,
    format_rfc3339,
    new_uuid4,
    utc_now,
)
from workhub.domain.feishu import FeishuCardAction
from workhub.errors import ApplicationError
from workhub.storage import Database


@dataclass(frozen=True, slots=True)
class CreatedAction:
    action: PendingAction
    token: str


@dataclass(frozen=True, slots=True)
class ActionClaim:
    action: PendingAction
    actor: ActorContext
    claimed: bool


class PendingActionRepository:
    def __init__(self, database: Database, audit: AuditWriter, *, ttl_seconds: int = 600) -> None:
        self.database = database
        self.audit = audit
        self.ttl_seconds = ttl_seconds

    async def create(
        self,
        actor: ActorContext,
        session_id: UUID,
        *,
        turn_id: UUID,
        conversation_id: str,
        mcp_client_key: str,
        tool_name: str,
        arguments: dict[str, Any],
        confirmation_summary: dict[str, Any],
    ) -> CreatedAction:
        canonical = canonical_json(arguments)
        args_hash = sha256_text(canonical)
        token = secrets.token_urlsafe(32)
        action_id = new_uuid4()
        idempotency_key = f"workhub:{action_id}"
        now = utc_now()
        created_at = format_rfc3339(now)
        expires_at = format_rfc3339(now + timedelta(seconds=self.ttl_seconds))
        async with self.database.transaction(write=True) as connection:
            turn = await (
                await connection.execute(
                    """
                    SELECT status FROM session_turns
                    WHERE id = ? AND employee_id = ? AND session_id = ?
                    """,
                    (str(turn_id), str(actor.employee_id), str(session_id)),
                )
            ).fetchone()
            if turn is None or turn["status"] != "running":
                raise ApplicationError(
                    "turn_not_running",
                    "Pending action requires the current running turn.",
                    status_code=409,
                )
            await connection.execute(
                """
                INSERT INTO pending_actions(
                    id, token_hash, employee_id, session_id, channel_identity_id,
                    conversation_id, mcp_client_key, tool_name, canonical_args_json,
                    args_hash, confirmation_summary_json, idempotency_key, status,
                    expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    str(action_id),
                    sha256_text(token),
                    str(actor.employee_id),
                    str(session_id),
                    str(actor.channel_identity_id),
                    conversation_id,
                    mcp_client_key,
                    tool_name,
                    canonical,
                    args_hash,
                    canonical_json(confirmation_summary),
                    idempotency_key,
                    expires_at,
                    created_at,
                ),
            )
            seq_row = await (
                await connection.execute(
                    """
                    UPDATE agent_sessions
                    SET next_seq = next_seq + 1, updated_at = ?
                    WHERE id = ? AND employee_id = ?
                    RETURNING next_seq - 1 AS seq
                    """,
                    (created_at, str(session_id), str(actor.employee_id)),
                )
            ).fetchone()
            if seq_row is None:
                raise ApplicationError(
                    "session_not_found", "Agent session was not found.", status_code=404
                )
            await connection.execute(
                """
                INSERT INTO session_events(
                    id, turn_id, session_id, employee_id, seq, event_type,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending_action_created', ?, ?)
                """,
                (
                    str(new_uuid4()),
                    str(turn_id),
                    str(session_id),
                    str(actor.employee_id),
                    int(seq_row["seq"]),
                    canonical_json(
                        {
                            "action_id": str(action_id),
                            "tool_name": tool_name,
                            "args_hash": args_hash,
                        }
                    ),
                    created_at,
                ),
            )
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="pending_action.created",
                    actor_type="employee",
                    actor_id=str(actor.employee_id),
                    subject_type="pending_action",
                    subject_id=str(action_id),
                    summary={
                        "client_key": mcp_client_key,
                        "tool_name": tool_name,
                        "args_hash": args_hash,
                    },
                ),
            )
        return CreatedAction(await self.get(action_id), token)

    async def set_card_message(self, action_id: UUID, message_id: str) -> PendingAction:
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                """
                UPDATE pending_actions SET card_message_id = ?
                WHERE id = ? AND status = 'pending' AND card_message_id IS NULL
                """,
                (message_id, str(action_id)),
            )
            if cursor.rowcount != 1:
                raise ApplicationError(
                    "pending_action_not_pending",
                    "Pending action is no longer available.",
                    status_code=409,
                )
        return await self.get(action_id)

    async def cancel_delivery_failure(self, action_id: UUID, error_code: str) -> PendingAction:
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                """UPDATE pending_actions SET status = 'cancelled', error_code = ?, completed_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (error_code, timestamp, str(action_id)),
            )
        return await self.get(action_id)

    async def validate_and_claim(self, callback: FeishuCardAction) -> ActionClaim:
        token_hash = sha256_text(callback.action_token)
        now = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            row = await _callback_row(connection, token_hash)
            if row is None:
                raise ApplicationError(
                    "invalid_action_token", "Confirmation action is invalid.", status_code=404
                )
            _validate_callback(row, callback)
            action = _action(row)
            if action.status != "pending":
                return ActionClaim(action, _actor(row), False)
            if action.expires_at <= now:
                await connection.execute(
                    """
                    UPDATE pending_actions SET status = 'expired', completed_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, str(action.action_id)),
                )
                return ActionClaim(
                    await _get_action_in(connection, action.action_id), _actor(row), False
                )
            if callback.decision == "cancel":
                target: Literal["cancelled", "executing"] = "cancelled"
                started_at = None
                completed_at = now
            else:
                target = "executing"
                started_at = now
                completed_at = None
            cursor = await connection.execute(
                """UPDATE pending_actions SET status = ?, started_at = ?, completed_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (target, started_at, completed_at, str(action.action_id)),
            )
            current = await _get_action_in(connection, action.action_id)
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type=f"pending_action.{target}",
                    actor_type="employee",
                    actor_id=str(action.employee_id),
                    subject_type="pending_action",
                    subject_id=str(action.action_id),
                    summary={"status": target},
                ),
            )
        return ActionClaim(current, _actor(row), cursor.rowcount == 1)

    async def finish(
        self,
        action_id: UUID,
        *,
        status: Literal["succeeded", "failed"],
        result_summary: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> PendingAction:
        timestamp = format_rfc3339(utc_now())
        safe_result = redact_audit_value(result_summary) if result_summary is not None else None
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                """UPDATE pending_actions SET status = ?, result_summary_json = ?, error_code = ?,
                   completed_at = ? WHERE id = ? AND status = 'executing'""",
                (
                    status,
                    canonical_json(safe_result) if safe_result is not None else None,
                    error_code,
                    timestamp,
                    str(action_id),
                ),
            )
            if cursor.rowcount != 1:
                current = await _get_action_in(connection, action_id)
                return current
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type=f"pending_action.{status}",
                    subject_type="pending_action",
                    subject_id=str(action_id),
                    summary={"status": status},
                    error_code=error_code,
                ),
            )
        return await self.get(action_id)

    async def get(self, action_id: UUID) -> PendingAction:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM pending_actions WHERE id = ?", (str(action_id),)
                )
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "pending_action_not_found", "Pending action was not found.", status_code=404
            )
        return _action(row)

    async def expire_pending(self) -> int:
        timestamp = format_rfc3339(utc_now())
        async with self.database.transaction(write=True) as connection:
            cursor = await connection.execute(
                """UPDATE pending_actions SET status = 'expired', completed_at = ?
                   WHERE status = 'pending' AND expires_at <= ?""",
                (timestamp, timestamp),
            )
        return cursor.rowcount

    async def list_executing(self) -> list[PendingAction]:
        async with self.database.connect() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM pending_actions WHERE status = 'executing' ORDER BY created_at"
                )
            ).fetchall()
        return [_action(row) for row in rows]

    async def actor_for(self, action: PendingAction) -> ActorContext:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT i.id AS identity_id, e.* FROM channel_identities i
                    JOIN employees e ON e.id = i.employee_id
                    WHERE i.id = ? AND i.binding_status = 'bound'
                      AND e.id = ? AND e.status = 'active'
                    """,
                    (str(action.channel_identity_id), str(action.employee_id)),
                )
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "action_actor_unavailable",
                "Action employee identity is no longer active.",
                status_code=403,
            )
        return ActorContext(
            employee_id=UUID(str(row["id"])),
            employee_no=str(row["employee_no"]),
            display_name=str(row["display_name"]),
            department=str(row["department"]),
            manager_employee_id=UUID(str(row["manager_employee_id"]))
            if row["manager_employee_id"]
            else None,
            timezone=str(row["timezone"]),
            channel_identity_id=UUID(str(row["identity_id"])),
        )

    async def address_for(self, action: PendingAction) -> ChannelAddress:
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT app_id FROM channel_identities WHERE id = ?",
                    (str(action.channel_identity_id),),
                )
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "action_address_unavailable", "Action address is unavailable.", status_code=404
            )
        return ChannelAddress(
            app_id=str(row["app_id"]),
            conversation_type="private",
            conversation_id=action.conversation_id,
        )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _callback_row(connection: aiosqlite.Connection, token_hash: str) -> aiosqlite.Row | None:
    return await (
        await connection.execute(
            """SELECT p.*, i.app_id, i.platform_user_id, i.binding_status,
                      e.employee_no, e.display_name, e.department, e.manager_employee_id,
                      e.timezone, e.status AS employee_status
               FROM pending_actions p JOIN channel_identities i ON i.id = p.channel_identity_id
               JOIN employees e ON e.id = p.employee_id WHERE p.token_hash = ?""",
            (token_hash,),
        )
    ).fetchone()


def _validate_callback(row: aiosqlite.Row, callback: FeishuCardAction) -> None:
    if (
        row["binding_status"] != "bound"
        or row["employee_status"] != "active"
        or str(row["app_id"]) != callback.app_id
        or str(row["platform_user_id"]) != callback.platform_user_id
    ):
        raise ApplicationError(
            "action_actor_mismatch", "Confirmation identity is not authorized.", status_code=403
        )
    if (
        str(row["conversation_id"]) != callback.conversation_id
        or row["card_message_id"] is None
        or str(row["card_message_id"]) != callback.message_id
    ):
        raise ApplicationError(
            "action_address_mismatch", "Confirmation address does not match.", status_code=403
        )
    canonical = str(row["canonical_args_json"])
    if sha256_text(canonical) != str(row["args_hash"]):
        raise ApplicationError(
            "action_arguments_tampered",
            "Stored action arguments failed integrity validation.",
            status_code=409,
        )


def _actor(row: aiosqlite.Row) -> ActorContext:
    return ActorContext(
        employee_id=UUID(str(row["employee_id"])),
        employee_no=str(row["employee_no"]),
        display_name=str(row["display_name"]),
        department=str(row["department"]),
        manager_employee_id=UUID(str(row["manager_employee_id"]))
        if row["manager_employee_id"]
        else None,
        timezone=str(row["timezone"]),
        channel_identity_id=UUID(str(row["channel_identity_id"])),
    )


async def _get_action_in(connection: aiosqlite.Connection, action_id: UUID) -> PendingAction:
    row = await (
        await connection.execute("SELECT * FROM pending_actions WHERE id = ?", (str(action_id),))
    ).fetchone()
    if row is None:
        raise ApplicationError(
            "pending_action_not_found", "Pending action was not found.", status_code=404
        )
    return _action(row)


def _action(row: aiosqlite.Row) -> PendingAction:
    canonical = str(row["canonical_args_json"])
    return PendingAction(
        action_id=UUID(str(row["id"])),
        employee_id=UUID(str(row["employee_id"])),
        session_id=UUID(str(row["session_id"])),
        channel_identity_id=UUID(str(row["channel_identity_id"])),
        conversation_id=str(row["conversation_id"]),
        card_message_id=str(row["card_message_id"]) if row["card_message_id"] else None,
        mcp_client_key=str(row["mcp_client_key"]),
        tool_name=str(row["tool_name"]),
        canonical_arguments=cast(dict[str, Any], json.loads(canonical)),
        args_sha256=str(row["args_hash"]),
        confirmation_summary=cast(
            dict[str, Any], json.loads(str(row["confirmation_summary_json"]))
        ),
        idempotency_key=str(row["idempotency_key"]),
        status=cast(PendingActionStatus, str(row["status"])),
        result_summary=cast(dict[str, Any], json.loads(str(row["result_summary_json"])))
        if row["result_summary_json"]
        else None,
        error_code=str(row["error_code"]) if row["error_code"] else None,
        expires_at=str(row["expires_at"]),
        created_at=str(row["created_at"]),
        started_at=str(row["started_at"]) if row["started_at"] else None,
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
    )
