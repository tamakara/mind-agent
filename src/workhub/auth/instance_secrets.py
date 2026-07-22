import secrets

from workhub.domain import format_rfc3339, utc_now
from workhub.storage import Database


async def get_or_create_instance_secret(database: Database, name: str) -> str:
    candidate = secrets.token_urlsafe(48)
    created_at = format_rfc3339(utc_now())
    async with database.transaction(write=True) as connection:
        await connection.execute(
            """INSERT INTO instance_secrets(name, secret, created_at)
               VALUES (?, ?, ?) ON CONFLICT(name) DO NOTHING""",
            (name, candidate, created_at),
        )
        row = await (
            await connection.execute(
                "SELECT secret FROM instance_secrets WHERE name = ?", (name,)
            )
        ).fetchone()
    if row is None:
        raise RuntimeError("Instance secret could not be initialized")
    return str(row["secret"])
