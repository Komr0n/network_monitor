"""
Database configuration and session management.
"""
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy import text

from app.config import DATABASE_URL
from app.models.models import Base

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,  # SQLite doesn't support connection pooling well
    future=True
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


async def _get_table_columns(conn, table_name: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
    return {row[1] for row in result.fetchall()}


async def _add_missing_columns(conn, table_name: str, column_statements: Iterable[tuple[str, str]]) -> None:
    existing_columns = await _get_table_columns(conn, table_name)
    for column_name, column_sql in column_statements:
        if column_name in existing_columns:
            continue
        await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}"))


async def _ensure_sqlite_schema_updates(conn) -> None:
    """Add newly introduced columns to existing SQLite tables."""
    if conn.dialect.name != "sqlite":
        return

    await _add_missing_columns(
        conn,
        "providers",
        [
            ("check_type", "check_type VARCHAR(10) NOT NULL DEFAULT 'AUTO'"),
            ("check_port", "check_port INTEGER"),
            ("check_path", "check_path VARCHAR(255)"),
            ("dns_expected_value", "dns_expected_value VARCHAR(255)"),
            ("maintenance_mode", "maintenance_mode INTEGER NOT NULL DEFAULT 0"),
            ("maintenance_note", "maintenance_note VARCHAR(255)"),
            ("maintenance_started_at", "maintenance_started_at DATETIME"),
            ("maintenance_window_start", "maintenance_window_start DATETIME"),
            ("maintenance_window_end", "maintenance_window_end DATETIME"),
            ("offline_since", "offline_since DATETIME"),
            ("last_check_method", "last_check_method VARCHAR(50)"),
            ("last_error", "last_error VARCHAR(500)"),
        ],
    )

    await _add_missing_columns(
        conn,
        "status_logs",
        [
            ("check_method", "check_method VARCHAR(50)"),
            ("details", "details VARCHAR(500)"),
        ],
    )

    await conn.execute(text("UPDATE providers SET check_type = 'AUTO' WHERE check_type IS NULL OR check_type = ''"))
    await conn.execute(text("UPDATE providers SET maintenance_mode = 0 WHERE maintenance_mode IS NULL"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_alert_logs_provider_sent_at ON alert_logs(provider_id, sent_at)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_providers_status_last_checked ON providers(current_status, last_checked)"))


async def init_db():
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_sqlite_schema_updates(conn)


async def get_db() -> AsyncSession:
    """Dependency to get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
