"""
SQLite backup management helpers.
"""
from __future__ import annotations

import sqlite3
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import DATABASE_URL
from app.time_utils import utc_now


ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass
class BackupInfo:
    filename: str
    path: Path
    size_bytes: int
    created_at: datetime


def get_database_file_path() -> Path:
    """Resolve the SQLite database path from DATABASE_URL."""
    database_path = make_url(DATABASE_URL).database
    if not database_path:
        raise ValueError("DATABASE_URL does not point to a file-based database")

    path = Path(database_path)
    if path.is_absolute():
        return path
    return (ROOT_DIR / path).resolve()


def get_backup_directory() -> Path:
    """Return the directory where SQLite backups are stored."""
    db_path = get_database_file_path()
    return db_path.parent / "backups"


def list_backups(limit: int = 20) -> list[BackupInfo]:
    """List recent SQLite backup files."""
    backup_dir = get_backup_directory()
    if not backup_dir.exists():
        return []

    backup_files = sorted(
        [path for path in backup_dir.glob("network_monitor_*.db") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    results: list[BackupInfo] = []
    for path in backup_files[:limit]:
        stat = path.stat()
        results.append(
            BackupInfo(
                filename=path.name,
                path=path,
                size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(tzinfo=None),
            )
        )
    return results


def create_backup() -> BackupInfo:
    """Create a timestamped copy of the SQLite database."""
    db_path = get_database_file_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    backup_dir = get_backup_directory()
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = utc_now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"network_monitor_{timestamp}.db"

    with sqlite3.connect(db_path) as source_connection, sqlite3.connect(backup_path) as backup_connection:
        source_connection.backup(backup_connection)

    shutil.copystat(db_path, backup_path)

    stat = backup_path.stat()
    return BackupInfo(
        filename=backup_path.name,
        path=backup_path,
        size_bytes=stat.st_size,
        created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(tzinfo=None),
    )


def resolve_backup_file(filename: str) -> Path:
    """Resolve a requested backup file safely within the backup directory."""
    candidate = (get_backup_directory() / filename).resolve()
    backup_dir = get_backup_directory().resolve()
    if backup_dir not in candidate.parents or not candidate.is_file():
        raise FileNotFoundError(filename)
    return candidate
