import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from workhub.domain import format_rfc3339, utc_now

if TYPE_CHECKING:
    from workhub.storage.database import Database


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- statement --\n".join(self.statements).encode()
        return hashlib.sha256(payload).hexdigest()


INITIAL_SCHEMA = Migration(
    version=1,
    name="initial_schema",
    statements=(
        """
        CREATE TABLE admin_users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE admin_sessions (
            id TEXT PRIMARY KEY,
            admin_user_id TEXT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            csrf_token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE admin_login_attempts (
            id TEXT PRIMARY KEY,
            username_hash TEXT NOT NULL,
            ip_hash TEXT NOT NULL,
            succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE employees (
            id TEXT PRIMARY KEY,
            employee_no TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            department TEXT NOT NULL,
            manager_employee_id TEXT REFERENCES employees(id) ON DELETE RESTRICT,
            timezone TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (manager_employee_id IS NULL OR manager_employee_id <> id)
        )
        """,
        """
        CREATE TABLE channel_identities (
            id TEXT PRIMARY KEY,
            channel TEXT NOT NULL CHECK (channel = 'feishu'),
            app_id TEXT NOT NULL,
            platform_user_id TEXT NOT NULL,
            display_name TEXT,
            binding_status TEXT NOT NULL CHECK (binding_status IN ('unbound', 'bound')),
            employee_id TEXT REFERENCES employees(id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (channel, app_id, platform_user_id),
            CHECK ((binding_status = 'bound' AND employee_id IS NOT NULL) OR
                   (binding_status = 'unbound' AND employee_id IS NULL))
        )
        """,
        """
        CREATE TABLE agent_sessions (
            id TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL UNIQUE REFERENCES employees(id) ON DELETE CASCADE,
            next_seq INTEGER NOT NULL DEFAULT 1 CHECK (next_seq >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, employee_id)
        )
        """,
        """
        CREATE TABLE session_turns (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            employee_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('normal', 'confirmation')),
            seq_lo INTEGER NOT NULL CHECK (seq_lo >= 1),
            seq_hi INTEGER CHECK (seq_hi IS NULL OR seq_hi >= seq_lo),
            headline TEXT,
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            pending_action_id TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (session_id, employee_id)
                REFERENCES agent_sessions(id, employee_id) ON DELETE CASCADE,
            UNIQUE (id, session_id, employee_id),
            UNIQUE (employee_id, session_id, seq_lo)
        )
        """,
        """
        CREATE TABLE session_events (
            id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            employee_id TEXT NOT NULL,
            seq INTEGER NOT NULL CHECK (seq >= 1),
            event_type TEXT NOT NULL,
            text TEXT,
            tool_call_id TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (turn_id, session_id, employee_id)
                REFERENCES session_turns(id, session_id, employee_id) ON DELETE CASCADE,
            UNIQUE (employee_id, session_id, seq)
        )
        """,
        """
        CREATE TABLE processed_channel_events (
            id TEXT PRIMARY KEY,
            channel TEXT NOT NULL,
            app_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            received_at TEXT NOT NULL,
            UNIQUE (channel, app_id, event_id)
        )
        """,
        """
        CREATE TABLE pending_actions (
            id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            employee_id TEXT NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
            session_id TEXT NOT NULL,
            channel_identity_id TEXT NOT NULL REFERENCES channel_identities(id) ON DELETE RESTRICT,
            conversation_id TEXT NOT NULL,
            mcp_client_key TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            canonical_args_json TEXT NOT NULL,
            args_hash TEXT NOT NULL,
            confirmation_summary_json TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'executing', 'succeeded', 'failed', 'cancelled', 'expired')
            ),
            result_summary_json TEXT,
            error_code TEXT,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            FOREIGN KEY (session_id, employee_id)
                REFERENCES agent_sessions(id, employee_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE model_settings (
            id TEXT PRIMARY KEY,
            provider_kind TEXT NOT NULL UNIQUE CHECK (provider_kind IN ('chat', 'embedding')),
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL,
            model TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE feishu_settings (
            id TEXT PRIMARY KEY,
            app_id TEXT NOT NULL UNIQUE,
            app_secret TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE mcp_clients (
            id TEXT PRIMARY KEY,
            client_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            headers_json TEXT,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE mcp_tools (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES mcp_clients(id) ON DELETE CASCADE,
            original_name TEXT NOT NULL,
            model_name TEXT NOT NULL UNIQUE,
            description TEXT,
            input_schema_json TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            UNIQUE (client_id, original_name)
        )
        """,
        """
        CREATE TABLE mcp_tool_settings (
            tool_id TEXT PRIMARY KEY REFERENCES mcp_tools(id) ON DELETE CASCADE,
            allowlisted INTEGER NOT NULL DEFAULT 0 CHECK (allowlisted IN (0, 1)),
            policy TEXT NOT NULL DEFAULT 'deny' CHECK (policy IN ('allow', 'confirm', 'deny')),
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE knowledge_nodes (
            id TEXT PRIMARY KEY,
            parent_id TEXT REFERENCES knowledge_nodes(id) ON DELETE RESTRICT,
            node_type TEXT NOT NULL CHECK (node_type IN ('directory', 'document')),
            name TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            reconciliation_required INTEGER NOT NULL DEFAULT 0
                CHECK (reconciliation_required IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (parent_id, name)
        )
        """,
        """
        CREATE TABLE knowledge_document_versions (
            id TEXT PRIMARY KEY,
            document_node_id TEXT NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
            version INTEGER NOT NULL CHECK (version >= 1),
            content_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            status TEXT NOT NULL CHECK (status IN ('queued', 'indexing', 'active', 'failed')),
            created_at TEXT NOT NULL,
            UNIQUE (document_node_id, version)
        )
        """,
        """
        CREATE TABLE knowledge_index_jobs (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL UNIQUE
                REFERENCES knowledge_document_versions(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('queued', 'indexing', 'succeeded', 'failed')),
            error_code TEXT,
            error_json TEXT,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT
        )
        """,
        """
        CREATE TABLE knowledge_generations (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES knowledge_document_versions(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'retired', 'failed')),
            index_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            activated_at TEXT
        )
        """,
        """
        CREATE TABLE knowledge_chunks (
            id TEXT PRIMARY KEY,
            generation_id TEXT NOT NULL REFERENCES knowledge_generations(id) ON DELETE CASCADE,
            chunk_no INTEGER NOT NULL CHECK (chunk_no >= 0),
            heading_path TEXT,
            line_start INTEGER NOT NULL CHECK (line_start >= 1),
            line_end INTEGER NOT NULL CHECK (line_end >= line_start),
            content TEXT NOT NULL,
            UNIQUE (generation_id, chunk_no)
        )
        """,
        """
        CREATE TABLE audit_events (
            id TEXT PRIMARY KEY,
            actor_type TEXT NOT NULL,
            actor_id TEXT,
            event_type TEXT NOT NULL,
            subject_type TEXT,
            subject_id TEXT,
            summary_json TEXT NOT NULL,
            error_code TEXT,
            request_id TEXT,
            duration_ms REAL CHECK (duration_ms IS NULL OR duration_ms >= 0),
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_admin_sessions_expiry ON admin_sessions(expires_at)",
        """
        CREATE INDEX idx_login_attempts_window
        ON admin_login_attempts(username_hash, ip_hash, created_at)
        """,
        "CREATE INDEX idx_identities_employee ON channel_identities(employee_id, binding_status)",
        """
        CREATE INDEX idx_turns_isolation
        ON session_turns(employee_id, session_id, seq_lo, seq_hi)
        """,
        "CREATE INDEX idx_events_isolation ON session_events(employee_id, session_id, seq)",
        "CREATE INDEX idx_events_turn ON session_events(employee_id, session_id, turn_id, seq)",
        """
        CREATE INDEX idx_pending_employee_status
        ON pending_actions(employee_id, status, expires_at)
        """,
        "CREATE INDEX idx_knowledge_parent ON knowledge_nodes(parent_id, name)",
        "CREATE INDEX idx_knowledge_jobs_status ON knowledge_index_jobs(status, created_at)",
        "CREATE INDEX idx_audit_created ON audit_events(created_at, event_type)",
        "CREATE INDEX idx_audit_actor ON audit_events(actor_type, actor_id, created_at)",
    ),
)

MIGRATIONS = (INITIAL_SCHEMA,)


async def apply_migrations(database: "Database") -> None:
    async with database.transaction(write=True) as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        cursor = await connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )
        applied = {int(row["version"]): row for row in await cursor.fetchall()}
        for migration in MIGRATIONS:
            existing = applied.get(migration.version)
            if existing is not None:
                if existing["name"] != migration.name or existing["checksum"] != migration.checksum:
                    raise RuntimeError(
                        f"Migration {migration.version} does not match applied schema"
                    )
                continue
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    format_rfc3339(utc_now()),
                ),
            )
