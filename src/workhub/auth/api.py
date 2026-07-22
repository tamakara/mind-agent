from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, SecretStr

from workhub.auth.service import AdminPrincipal, AuthService
from workhub.config import WorkHubSettings

TOKEN_COOKIE = "workhub_admin_token"


class LoginRequest(BaseModel):
    username: str
    password: SecretStr


class SessionResponse(BaseModel):
    admin_user_id: str
    username: str
    expires_at: str


class BootstrapStatusResponse(BaseModel):
    initialized: bool


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

    @router.get("/bootstrap-status")
    async def bootstrap_status(request: Request) -> BootstrapStatusResponse:
        service: AuthService = request.app.state.auth_service
        return BootstrapStatusResponse(initialized=await service.is_bootstrapped())

    @router.post("/login")
    async def login(payload: LoginRequest, request: Request, response: Response) -> SessionResponse:
        service: AuthService = request.app.state.auth_service
        settings: WorkHubSettings = request.app.state.settings
        client_ip = request.client.host if request.client is not None else "unknown"
        authenticated = await service.login(
            payload.username, payload.password.get_secret_value(), client_ip
        )
        response.set_cookie(
            TOKEN_COOKIE,
            authenticated.token,
            max_age=settings.admin_session_ttl_seconds,
            httponly=True,
            secure=settings.admin_cookie_secure,
            samesite="strict",
            path="/",
        )
        principal = authenticated.principal
        return SessionResponse(
            admin_user_id=principal.admin_user_id,
            username=principal.username,
            expires_at=principal.expires_at,
        )

    @router.get("/session")
    async def session(request: Request) -> SessionResponse:
        principal: AdminPrincipal = request.state.admin
        return SessionResponse(
            admin_user_id=principal.admin_user_id,
            username=principal.username,
            expires_at=principal.expires_at,
        )

    @router.post("/logout", status_code=204)
    async def logout(response: Response) -> None:
        response.delete_cookie(TOKEN_COOKIE, path="/")

    return router
