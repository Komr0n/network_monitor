"""
Configuration settings for the Network Monitoring System.
"""
import os
from pathlib import Path

from dotenv import load_dotenv


def get_bool_env(name: str, default: str) -> bool:
    """Read a boolean environment variable."""
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def get_csv_env(name: str, default: str = "") -> list[str]:
    """Read a comma-separated environment variable."""
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# Load .env from project root (parent of app directory)
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./network_monitor.db")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_CA_BUNDLE = os.getenv("TELEGRAM_CA_BUNDLE", "").strip()

# Monitoring Settings
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))  # seconds
FAIL_THRESHOLD = int(os.getenv("FAIL_THRESHOLD", "3"))  # consecutive failures
TIMEOUT = int(os.getenv("TIMEOUT", "2"))  # ping timeout in seconds

# JWT Authentication
DEFAULT_JWT_SECRET = "your-secret-key-change-this-in-production"
JWT_SECRET = os.getenv("JWT_SECRET", DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION = int(os.getenv("JWT_EXPIRATION", "86400"))  # 24 hours in seconds

# Application
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
DEBUG = get_bool_env("DEBUG", "false")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "").strip()
UVICORN_FORWARDED_ALLOW_IPS = os.getenv("UVICORN_FORWARDED_ALLOW_IPS", "127.0.0.1").strip() or "127.0.0.1"

# Production hardening
TRUSTED_HOSTS = get_csv_env("TRUSTED_HOSTS", "*")
FORCE_HTTPS = get_bool_env("FORCE_HTTPS", "false")
SESSION_HTTPS_ONLY = get_bool_env("SESSION_HTTPS_ONLY", "false")
AUTO_CREATE_ADMIN = get_bool_env("AUTO_CREATE_ADMIN", "true" if DEBUG else "false")
DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "admin").strip() or "admin"
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
