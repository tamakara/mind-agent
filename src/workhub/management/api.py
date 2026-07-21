import asyncio
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from workhub.domain import Page
from workhub.feishu import FeishuGateway
from workhub.mcp import MCPManager
from workhub.storage import Database


class OverviewStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    employees_total: int = Field(ge=0)
    employees_active: int = Field(ge=0)
    identities_unbound: int = Field(ge=0)
    knowledge_documents: int = Field(ge=0)
    knowledge_index_failed: int = Field(ge=0)
    mcp_clients: int = Field(ge=0)
    mcp_tools_exposed: int = Field(ge=0)
    pending_actions: int = Field(ge=0)
    feishu_state: str
    mock_oa_state: str


class AuditEventView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    actor_type: str
    actor_id: str | None
    event_type: str
    subject_type: str | None
    subject_id: str | None
    summary: dict[str, Any]
    error_code: str | None
    request_id: str | None
    duration_ms: float | None
    created_at: str


def create_management_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["management"])

    @router.get("/overview", response_model=OverviewStats)
    async def overview(request: Request) -> OverviewStats:
        database: Database = request.app.state.database
        async with database.connect() as connection:
            values = await asyncio.gather(
                _count(connection, "SELECT COUNT(*) FROM employees"),
                _count(connection, "SELECT COUNT(*) FROM employees WHERE status = 'active'"),
                _count(
                    connection,
                    "SELECT COUNT(*) FROM channel_identities WHERE binding_status = 'unbound'",
                ),
                _count(
                    connection,
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE node_type = 'document'",
                ),
                _count(
                    connection,
                    "SELECT COUNT(*) FROM knowledge_index_jobs WHERE status = 'failed'",
                ),
                _count(connection, "SELECT COUNT(*) FROM mcp_clients"),
                _count(
                    connection,
                    """SELECT COUNT(*) FROM mcp_tool_settings
                       WHERE allowlisted = 1 AND policy <> 'deny'""",
                ),
                _count(
                    connection,
                    "SELECT COUNT(*) FROM pending_actions WHERE status IN ('pending', 'executing')",
                ),
            )
        gateway: FeishuGateway | None = request.app.state.feishu_gateway
        feishu_state = gateway.status().state if gateway is not None else "disabled"
        mock_oa_state = await _mock_oa_state(request)
        return OverviewStats(
            employees_total=values[0],
            employees_active=values[1],
            identities_unbound=values[2],
            knowledge_documents=values[3],
            knowledge_index_failed=values[4],
            mcp_clients=values[5],
            mcp_tools_exposed=values[6],
            pending_actions=values[7],
            feishu_state=feishu_state,
            mock_oa_state=mock_oa_state,
        )

    @router.get("/audit-events", response_model=Page[AuditEventView])
    async def audit_events(
        request: Request,
        employee_id: str | None = None,
        event: str | None = None,
        tool: str | None = None,
        action_id: str | None = None,
        business_id: str | None = None,
        result: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> Page[AuditEventView]:
        conditions: list[str] = []
        parameters: list[object] = []
        if employee_id:
            conditions.append("(actor_id = ? OR subject_id = ? OR summary_json LIKE ?)")
            parameters.extend((employee_id, employee_id, _contains(employee_id)))
        if event:
            conditions.append("event_type LIKE ?")
            parameters.append(_contains(event))
        if tool:
            conditions.append("summary_json LIKE ?")
            parameters.append(_contains(tool))
        if action_id:
            conditions.append("(subject_id = ? OR summary_json LIKE ?)")
            parameters.extend((action_id, _contains(action_id)))
        if business_id:
            conditions.append("summary_json LIKE ?")
            parameters.append(_contains(business_id))
        if result:
            conditions.append("(error_code LIKE ? OR summary_json LIKE ?)")
            parameters.extend((_contains(result), _contains(result)))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        database: Database = request.app.state.database
        async with database.connect() as connection:
            rows = await (
                await connection.execute(
                    f"""SELECT * FROM audit_events {where}
                        ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                    (*parameters, limit, offset),
                )
            ).fetchall()
            total = await (
                await connection.execute(
                    f"SELECT COUNT(*) FROM audit_events {where}", tuple(parameters)
                )
            ).fetchone()
        assert total is not None
        return Page(
            items=[_audit_event(row) for row in rows],
            total=int(total[0]),
            limit=limit,
            offset=offset,
        )

    @router.get("/mcp/health")
    async def mcp_health(request: Request) -> dict[str, dict[str, str | None]]:
        manager: MCPManager = request.app.state.mcp_manager
        return {
            key: {"state": item.state, "error_code": item.error_code}
            for key, item in manager.health().items()
        }

    return router


async def _count(connection: Any, query: str) -> int:
    row = await (await connection.execute(query)).fetchone()
    assert row is not None
    return int(row[0])


def _contains(value: str) -> str:
    return f"%{value.replace('%', '')}%"


def _audit_event(row: Any) -> AuditEventView:
    raw = json.loads(str(row["summary_json"]))
    summary = raw if isinstance(raw, dict) else {}
    return AuditEventView(
        event_id=str(row["id"]),
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]) if row["actor_id"] else None,
        event_type=str(row["event_type"]),
        subject_type=str(row["subject_type"]) if row["subject_type"] else None,
        subject_id=str(row["subject_id"]) if row["subject_id"] else None,
        summary=summary,
        error_code=str(row["error_code"]) if row["error_code"] else None,
        request_id=str(row["request_id"]) if row["request_id"] else None,
        duration_ms=float(row["duration_ms"]) if row["duration_ms"] is not None else None,
        created_at=str(row["created_at"]),
    )


async def _mock_oa_state(request: Request) -> str:
    configured = request.app.state.settings.mock_oa_mcp_url
    if not configured:
        return "disabled"
    parsed = urlsplit(configured)
    health_url = urlunsplit((parsed.scheme, parsed.netloc, "/readyz", "", ""))

    def check() -> str:
        try:
            with urlopen(health_url, timeout=1) as response:
                return "ready" if response.status == 200 else "error"
        except Exception:
            return "error"

    return await asyncio.to_thread(check)
