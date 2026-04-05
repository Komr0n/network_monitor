"""
Runtime environment bootstrap helpers.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path


def render_default_env() -> tuple[str, str]:
    """Render a zero-config .env and return it with the generated admin password."""
    admin_password = secrets.token_urlsafe(12)
    jwt_secret = secrets.token_urlsafe(48)
    local_timezone = getattr(datetime.now().astimezone().tzinfo, "key", "")

    lines = [
        "# Auto-generated bootstrap configuration",
        "# Profile: direct IP / LAN quickstart",
        "# This profile is intentionally permissive so the first launch works from another machine.",
        "# Tighten TRUSTED_HOSTS and session/HTTPS settings before Internet exposure.",
        "",
        "DATABASE_URL=sqlite+aiosqlite:///./network_monitor.db",
        "",
        "TELEGRAM_BOT_TOKEN=",
        "TELEGRAM_CHAT_ID=",
        "TELEGRAM_CA_BUNDLE=",
        "",
        "CHECK_INTERVAL=30",
        "FAIL_THRESHOLD=3",
        "TIMEOUT=2",
        "",
        f"JWT_SECRET={jwt_secret}",
        "JWT_EXPIRATION=86400",
        "SESSION_HTTPS_ONLY=false",
        "",
        "APP_HOST=0.0.0.0",
        "APP_PORT=8000",
        "DEBUG=false",
        "LOG_LEVEL=INFO",
        f"APP_TIMEZONE={local_timezone}",
        "UVICORN_FORWARDED_ALLOW_IPS=127.0.0.1",
        "",
        "TRUSTED_HOSTS=*",
        "FORCE_HTTPS=false",
        "",
        "AUTO_CREATE_ADMIN=true",
        "DEFAULT_ADMIN_USERNAME=admin",
        f"DEFAULT_ADMIN_PASSWORD={admin_password}",
        "",
    ]
    return "\n".join(lines), admin_password


def ensure_env_file(env_path: str | Path = ".env") -> tuple[bool, str | None]:
    """Create a default .env if it does not exist."""
    env_path = Path(env_path)
    if env_path.exists():
        return False, None

    env_text, admin_password = render_default_env()
    env_path.write_text(env_text, encoding="utf-8", newline="\n")
    return True, admin_password
