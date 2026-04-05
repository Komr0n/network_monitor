"""
Entry point for the Network Monitoring System.
"""
from pathlib import Path
import sys

import uvicorn

from app.env_bootstrap import ensure_env_file


def bootstrap_env() -> None:
    """Create a default .env for first run when the file is missing."""
    env_path = Path(__file__).resolve().parent / ".env"
    created, admin_password = ensure_env_file(env_path)
    if not created:
        return

    print(f"Created default configuration at {env_path}", file=sys.stderr)
    print("Bootstrap profile: direct IP / LAN quickstart", file=sys.stderr)
    print("Allowed hosts: any host (TRUSTED_HOSTS=*)", file=sys.stderr)
    if admin_password:
        print("Bootstrap admin user: admin", file=sys.stderr)
        print(f"Bootstrap admin password: {admin_password}", file=sys.stderr)
        print(
            "For reverse-proxy production, replace .env with deploy/linux/.env.linux.example.",
            file=sys.stderr
        )
        print(
            "For a long-running direct IP service, use deploy/linux/.env.linux.ip.example.",
            file=sys.stderr
        )


bootstrap_env()

from app.config import APP_HOST, APP_PORT, DEBUG, LOG_LEVEL, UVICORN_FORWARDED_ALLOW_IPS

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=APP_HOST,
        port=APP_PORT,
        reload=DEBUG,
        log_level=LOG_LEVEL.lower(),
        proxy_headers=True,
        forwarded_allow_ips=UVICORN_FORWARDED_ALLOW_IPS,
    )
