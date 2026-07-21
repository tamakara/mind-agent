from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, SecretStr

from workhub.auth.service import AdminPrincipal, AuthService
from workhub.config import WorkHubSettings

SESSION_COOKIE = "workhub_admin_session"
CSRF_COOKIE = "workhub_csrf"


class LoginRequest(BaseModel):
    username: str
    password: SecretStr


class SessionResponse(BaseModel):
    admin_user_id: str
    username: str
    expires_at: str


class LoginResponse(SessionResponse):
    csrf_token: str


class BootstrapStatusResponse(BaseModel):
    initialized: bool


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

    @router.get("/bootstrap-status")
    async def bootstrap_status(request: Request) -> BootstrapStatusResponse:
        service: AuthService = request.app.state.auth_service
        return BootstrapStatusResponse(initialized=await service.is_bootstrapped())

    @router.post("/login")
    async def login(payload: LoginRequest, request: Request, response: Response) -> LoginResponse:
        service: AuthService = request.app.state.auth_service
        settings: WorkHubSettings = request.app.state.settings
        client_ip = request.client.host if request.client is not None else "unknown"
        authenticated = await service.login(
            payload.username, payload.password.get_secret_value(), client_ip
        )
        max_age = settings.admin_session_ttl_seconds
        response.set_cookie(
            SESSION_COOKIE,
            authenticated.session_token,
            max_age=max_age,
            httponly=True,
            secure=settings.admin_cookie_secure,
            samesite="strict",
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            authenticated.csrf_token,
            max_age=max_age,
            httponly=False,
            secure=settings.admin_cookie_secure,
            samesite="strict",
            path="/",
        )
        principal = authenticated.principal
        return LoginResponse(
            admin_user_id=principal.admin_user_id,
            username=principal.username,
            expires_at=principal.expires_at,
            csrf_token=authenticated.csrf_token,
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
    async def logout(request: Request, response: Response) -> None:
        service: AuthService = request.app.state.auth_service
        principal: AdminPrincipal = request.state.admin
        await service.logout(principal)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")

    return router
