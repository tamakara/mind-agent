import json
from collections.abc import Callable
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from mock_oa_service.domain import (
    LeaveBalance,
    LeaveRequestStatus,
    LeaveSubmission,
    MockOAError,
)
from mock_oa_service.repository import LeaveRepository


def create_mcp_server(repository: Callable[[], LeaveRepository]) -> FastMCP[None]:
    server = FastMCP(
        "Mock OA",
        instructions="Annual leave tools for trusted WorkHub employee subjects.",
        streamable_http_path="/mcp",
        stateless_http=False,
        json_response=True,
    )

    @server.tool(
        description=(
            "Return the current annual leave balance. employee_id and employee_no "
            "must be injected by the trusted WorkHub execution wrapper."
        )
    )
    async def query_leave_balance(employee_id: str, employee_no: str) -> LeaveBalance:
        try:
            return await repository().balance(employee_id=employee_id, employee_no=employee_no)
        except MockOAError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        description=(
            "Submit an annual leave request. The trusted WorkHub wrapper injects the "
            "employee subject and idempotency key."
        )
    )
    async def submit_leave_request(
        start_date: str,
        end_date: str,
        leave_type: Literal["annual_leave"],
        employee_id: str,
        employee_no: str,
        idempotency_key: str,
        reason: str | None = None,
    ) -> LeaveSubmission:
        try:
            return await repository().submit(
                employee_id=employee_id,
                employee_no=employee_no,
                start_date=start_date,
                end_date=end_date,
                leave_type=leave_type,
                reason=reason,
                idempotency_key=idempotency_key,
            )
        except MockOAError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        description=(
            "Return one leave request owned by the trusted current employee. "
            "employee_id and employee_no are injected by WorkHub."
        )
    )
    async def query_leave_request_status(
        request_id: str, employee_id: str, employee_no: str
    ) -> LeaveRequestStatus:
        try:
            return await repository().status(
                employee_id=employee_id,
                employee_no=employee_no,
                request_id=request_id,
            )
        except MockOAError as exc:
            raise _tool_error(exc) from exc

    return server


def _tool_error(exc: MockOAError) -> ToolError:
    return ToolError(
        json.dumps(
            {"error": {"code": exc.code, "message": exc.message}},
            separators=(",", ":"),
        )
    )
