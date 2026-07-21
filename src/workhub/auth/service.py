import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite

from workhub.audit import AuditEvent, AuditWriter
from workhub.auth.passwords import ScryptPasswordHasher
from workhub.domain import format_rfc3339, new_uuid4, utc_now
from workhub.errors import ApplicationError
from workhub.storage import Database


class InvalidCredentialsError(ApplicationError):
    def __init__(self) -> None:
        super().__init__("invalid_credentials", "Invalid username or password.", status_code=401)


class LoginRateLimitedError(ApplicationError):
    def __init__(self) -> None:
        super().__init__("login_rate_limited", "Too many login attempts.", status_code=429)


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    admin_user_id: str
    username: str
    session_id: str
    csrf_token_hash: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    principal: AdminPrincipal
    session_token: str
    csrf_token: str


class AuthService:
    def __init__(
        self,
        database: Database,
        audit: AuditWriter,
        *,
        session_ttl_seconds: int,
        login_window_seconds: int,
        login_max_attempts: int,
        session_secret: str | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database = database
        self.audit = audit
        self.session_ttl = timedelta(seconds=session_ttl_seconds)
        self.login_window = timedelta(seconds=login_window_seconds)
        self.login_max_attempts = login_max_attempts
        self.session_secret = session_secret.encode() if session_secret else None
        self.clock = clock
        self.passwords = ScryptPasswordHasher()
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
            raise RuntimeError("Both bootstrap administrator username and password are required")
        normalized_username = username.strip()
        if not normalized_username:
            raise RuntimeError("Bootstrap administrator username must not be empty")
        password_hash = self.passwords.hash(password)
        timestamp = format_rfc3339(self.clock())
        admin_user_id = str(new_uuid4())
        async with self.database.transaction(write=True) as connection:
            count = await (await connection.execute("SELECT COUNT(*) FROM admin_users")).fetchone()
            assert count is not None
            if count[0] > 0:
                return False
            await connection.execute(
                """
                INSERT INTO admin_users(
                    id, username, password_hash, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?)
                """,
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

    async def login(self, username: str, password: str, client_ip: str) -> AuthenticatedSession:
        normalized_username = username.strip()
        username_hash = _fingerprint(normalized_username.lower())
        ip_hash = _fingerprint(client_ip)
        now = self.clock()
        now_text = format_rfc3339(now)
        cutoff = format_rfc3339(now - self.login_window)

        async with self.database.connect() as connection:
            user = await (
                await connection.execute(
                    """
                    SELECT id, username, password_hash, status
                    FROM admin_users WHERE username = ? COLLATE NOCASE
                    """,
                    (normalized_username,),
                )
            ).fetchone()
        encoded_hash = str(user["password_hash"]) if user is not None else self._dummy_password_hash
        password_valid = self.passwords.verify(password, encoded_hash)
        credentials_valid = user is not None and user["status"] == "active" and password_valid

        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session_id = str(new_uuid4())
        expires_at = format_rfc3339(now + self.session_ttl)
        principal: AdminPrincipal | None = None
        error: ApplicationError | None = None
        async with self.database.transaction(write=True) as connection:
            failed = await (
                await connection.execute(
                    """
                    SELECT COUNT(*) FROM admin_login_attempts
                    WHERE username_hash = ? AND ip_hash = ? AND succeeded = 0 AND created_at >= ?
                    """,
                    (username_hash, ip_hash, cutoff),
                )
            ).fetchone()
            assert failed is not None
            if failed[0] >= self.login_max_attempts:
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="admin.login_rate_limited",
                        summary={"username_hash": username_hash, "ip_hash": ip_hash},
                        error_code="login_rate_limited",
                    ),
                )
                error = LoginRateLimitedError()
            elif not credentials_valid:
                await self._record_attempt(
                    connection, username_hash, ip_hash, succeeded=False, created_at=now_text
                )
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="admin.login_failed",
                        summary={"username_hash": username_hash, "ip_hash": ip_hash},
                        error_code="invalid_credentials",
                    ),
                )
                error = InvalidCredentialsError()
            else:
                assert user is not None
                await connection.execute(
                    """
                    DELETE FROM admin_login_attempts
                    WHERE username_hash = ? AND ip_hash = ? AND succeeded = 0
                    """,
                    (username_hash, ip_hash),
                )
                await self._record_attempt(
                    connection, username_hash, ip_hash, succeeded=True, created_at=now_text
                )
                await connection.execute(
                    "DELETE FROM admin_sessions WHERE expires_at <= ?", (now_text,)
                )
                await connection.execute(
                    """
                    INSERT INTO admin_sessions(
                        id, admin_user_id, token_hash, csrf_token_hash,
                        expires_at, last_seen_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        user["id"],
                        _token_hash(session_token, self.session_secret),
                        _token_hash(csrf_token, self.session_secret),
                        expires_at,
                        now_text,
                        now_text,
                    ),
                )
                principal = AdminPrincipal(
                    admin_user_id=str(user["id"]),
                    username=str(user["username"]),
                    session_id=session_id,
                    csrf_token_hash=_token_hash(csrf_token, self.session_secret),
                    expires_at=expires_at,
                )
                await self.audit.write_in_transaction(
                    connection,
                    AuditEvent(
                        event_type="admin.login_succeeded",
                        actor_type="admin",
                        actor_id=principal.admin_user_id,
                        subject_type="admin_session",
                        subject_id=session_id,
                        summary={"ip_hash": ip_hash},
                    ),
                )
        if error is not None:
            raise error
        assert principal is not None
        return AuthenticatedSession(
            principal=principal,
            session_token=session_token,
            csrf_token=csrf_token,
        )

    async def authenticate(self, session_token: str | None) -> AdminPrincipal | None:
        if not session_token:
            return None
        now = format_rfc3339(self.clock())
        async with self.database.connect() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT u.id AS admin_user_id, u.username, s.id AS session_id,
                           s.csrf_token_hash, s.expires_at
                    FROM admin_sessions s
                    JOIN admin_users u ON u.id = s.admin_user_id
                    WHERE s.token_hash = ? AND s.expires_at > ? AND u.status = 'active'
                    """,
                    (_token_hash(session_token, self.session_secret), now),
                )
            ).fetchone()
        if row is None:
            return None
        return AdminPrincipal(
            admin_user_id=str(row["admin_user_id"]),
            username=str(row["username"]),
            session_id=str(row["session_id"]),
            csrf_token_hash=str(row["csrf_token_hash"]),
            expires_at=str(row["expires_at"]),
        )

    def verify_csrf(self, principal: AdminPrincipal, csrf_token: str | None) -> bool:
        return bool(
            csrf_token
            and hmac.compare_digest(
                _token_hash(csrf_token, self.session_secret), principal.csrf_token_hash
            )
        )

    async def logout(self, principal: AdminPrincipal) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                "DELETE FROM admin_sessions WHERE id = ? AND admin_user_id = ?",
                (principal.session_id, principal.admin_user_id),
            )
            await self.audit.write_in_transaction(
                connection,
                AuditEvent(
                    event_type="admin.logout",
                    actor_type="admin",
                    actor_id=principal.admin_user_id,
                    subject_type="admin_session",
                    subject_id=principal.session_id,
                    summary={},
                ),
            )

    async def _record_attempt(
        self,
        connection: aiosqlite.Connection,
        username_hash: str,
        ip_hash: str,
        *,
        succeeded: bool,
        created_at: str,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO admin_login_attempts(
                id, username_hash, ip_hash, succeeded, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (str(new_uuid4()), username_hash, ip_hash, int(succeeded), created_at),
        )


def _token_hash(token: str, secret: bytes | None) -> str:
    if secret is None:
        return hashlib.sha256(token.encode()).hexdigest()
    return hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()


def _fingerprint(value: str) -> str:
    return hashlib.sha256(f"workhub:{value}".encode()).hexdigest()
