from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LeaveStatus = Literal["pending_approval", "approved", "rejected"]


class LeaveBalance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    remaining_days: float = Field(ge=0)
    as_of: str


class LeaveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    employee_id: str
    start_date: str
    end_date: str
    leave_type: Literal["annual_leave"]
    reason: str | None
    working_days: float = Field(gt=0)
    status: LeaveStatus
    submitted_at: str
    updated_at: str


class LeaveSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    status: Literal["pending_approval"]
    submitted_at: str


class LeaveRequestStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    status: LeaveStatus
    updated_at: str


class MockOAError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
