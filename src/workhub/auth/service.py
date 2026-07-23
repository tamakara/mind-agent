import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from workhub.audit import AuditEvent, AuditWriter
from workhub.auth.passwords import PasswordHasher
from workhub.domain import format_rfc3339, new_uuid4, utc_now
from workhub.errors import ApplicationError
from workhub.storage import Database


class InvalidCredentialsError(ApplicationError):
    def __init__(self) -> None:
        super().__init__("invalid_credentials", "Invalid username or password.", status_code=401)


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    admin_user_id: str
    username: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class AuthenticatedToken:
    principal: AdminPrincipal
    token: str


class AuthService:
    def __init__(
        self,
        database: Database,
        audit: AuditWriter,
        *,
        token_ttl_seconds: int,
        signing_key: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database = database
        self.audit = audit
        self.token_ttl = timedelta(seconds=token_ttl_seconds)
        self.signing_key = signing_key
        self.clock = clock
        self.passwords = PasswordHasher()
        self._dummy_password_hash = self.passwords.hash("not-a-real-password")

    async def bootstrap(self, username: str | None, password: str | None) -> bool:
        async with self.database.connect() as connection:
            count = await (await connection.execute("SELECT COUNT(*) FROM admin_users")).fetchone()
        assert count is not None
        if count[0] > 0:
            return False
        if username is None and password is None:
            return False
        if not username or not password:
            raise RuntimeError("Both administrator username and password are required")
        normalized_username = username.strip()
        if not normalized_username:
            raise RuntimeError("Administrator username must not be empty")
        password_hash = self.passwords.hash(password)
        timestamp = format_rfc3339(self.clock())
        admin_user_id = str(new_uuid4())
        async with self.database.transaction(write=True) as connection:
            count = await (await connection.execute("SELECT COUNT(*) FROM admin_users")).fetchone()
            assert count is not None
            if count[0] > 0:
                return False
            await connection.execute(
                """INSERT INTO admin_users(
                    id, username, password_hash, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?)""",
                (admin_user_id, normalized_username, password_hash, timestamp, timestamp),
            )
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="admin.bootstrap",
                    actor_type="admin",
                    actor_id=admin_user_id,
                    subject_type="admin_user",
                    subject_id=admin_user_id,
                    summary={"username_hash": _fingerprint(normalized_username.lower())},
                ),
            )
        return True

    async def is_bootstrapped(self) -> bool:
        async with self.database.connect() as connection:
            row = await (await connection.execute("SELECT 1 FROM admin_users LIMIT 1")).fetchone()
        return row is not None

    async def login(self, username: str, password: str, client_ip: str) -> AuthenticatedToken:
        normalized_username = username.strip()
        async with self.database.connect() as connection:
            user = await (
                await connection.execute(
                    """SELECT id, username, password_hash, status
                       FROM admin_users WHERE username = ? COLLATE NOCASE""",
                    (normalized_username,),
                )
            ).fetchone()
        encoded_hash = str(user["password_hash"]) if user is not None else self._dummy_password_hash
        password_valid = self.passwords.verify(password, encoded_hash)
        if user is None or user["status"] != "active" or not password_valid:
            await self.audit.write(
                AuditEvent(
                    event_type="admin.login_failed",
                    summary={
                        "username_hash": _fingerprint(normalized_username.lower()),
                        "ip_hash": _fingerprint(client_ip),
                    },
                    error_code="invalid_credentials",
                )
            )
            raise InvalidCredentialsError()

        now = self.clock()
        expires = now + self.token_ttl
        principal = AdminPrincipal(
            admin_user_id=str(user["id"]),
            username=str(user["username"]),
            expires_at=format_rfc3339(expires),
        )
        payload = {
            "sub": principal.admin_user_id,
            "username": principal.username,
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
            "jti": secrets.token_urlsafe(16),
        }
        token = jwt.encode(payload, self.signing_key, algorithm="HS256")
        await self.audit.write(
            AuditEvent(
                event_type="admin.login_succeeded",
                actor_type="admin",
                actor_id=principal.admin_user_id,
                subject_type="admin_user",
                subject_id=principal.admin_user_id,
                summary={"ip_hash": _fingerprint(client_ip)},
            )
        )
        return AuthenticatedToken(principal=principal, token=token)

    async def authenticate(self, token: str | None) -> AdminPrincipal | None:
        payload = self._decode(token)
        if payload is None:
            return None
        admin_id = payload.get("sub")
        username = payload.get("username")
        expires_at = payload.get("exp")
        if not isinstance(admin_id, str) or not isinstance(username, str):
            return None
        if not isinstance(expires_at, int) or expires_at <= int(self.clock().timestamp()):
            return None
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT username, status FROM admin_users WHERE id = ?", (admin_id,)
                )
            ).fetchone()
        if row is None or row["status"] != "active" or row["username"] != username:
            return None
        return AdminPrincipal(
            admin_user_id=admin_id,
            username=username,
            expires_at=format_rfc3339(datetime.fromtimestamp(expires_at, tz=UTC)),
        )

    def _decode(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        try:
            payload = jwt.decode(
                token,
                self.signing_key,
                algorithms=["HS256"],
                options={
                    "verify_exp": False,
                    "require": ["sub", "username", "iat", "exp", "jti"],
                },
            )
            # Reject non-canonical base64url encodings that decode to the same payload.
            if jwt.encode(payload, self.signing_key, algorithm="HS256") != token:
                return None
            return payload
        except jwt.PyJWTError:
            return None


def _fingerprint(value: str) -> str:
    return hashlib.sha256(f"workhub:{value}".encode()).hexdigest()
