# Network Monitor

A production-ready network monitoring system with a built-in web dashboard, continuous scheduled checks, and Telegram notifications. Designed to be lightweight and fast with an asynchronous architecture using FastAPI.

## Features

- **Real-Time Web Dashboard**: Monitor the current status of all your network endpoints (ICMP ping, HTTP, etc.) via a built-in responsive web UI.
- **Provider Management**: Add, group, and manage network hosts directly from the UI. Features CSV import for bulk loading.
- **Background Checks & Scheduler**: Automatic periodical checks powered by `apscheduler` executing efficiently in the background.
- **Telegram Notifications**: Get instant alerts in a Telegram chat whenever a host goes offline or comes back online.
- **History & Logs**: Detailed logging and history tracking for downtime analysis.
- **Secure by Default**: 
  - JWT authentication and secure session management.
  - Auto-generated strong passwords on the first launch if not explicitly configured.
  - Cryptographically secure password hashing (PBKDF2/bcrypt) and CSRF protection.
- **Easy Deployment**: Runs natively on Windows Server, bare-metal Linux, or seamlessly behind an Nginx reverse proxy.

## Technology Stack

- **Backend**: FastAPI & Uvicorn (Python 3)
- **Database**: SQLite (async via `aiosqlite`) + SQLAlchemy ORM, Alembic for database migrations
- **Network protocols**: `icmplib` for fast asynchronous ICMP pinging, `httpx` for HTTP-based interactions
- **Templating**: Jinja2 + Bootstrap forms
- **Task Scheduling**: `apscheduler`

## Quick Start (Local / Development)

### 1. Prerequisites
- Python 3.10+
- (Optional, but recommended) A python virtual environment

### 2. Installation
Clone the repository, create a virtual environment, and install dependencies:

```bash
# Create the environment
python -m venv .venv

# Activate it (Linux/macOS)
source .venv/bin/activate
# Activate it (Windows)
.venv\Scripts\activate

# Install the required packages
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configuration
Copy the provided sample environment file.

```bash
# Linux/macOS
cp .env.example .env

# Windows
copy .env.example .env
```

### 4. Running the application
Run the `main.py` entrypoint.

```bash
python main.py
```

The application will start and the dashboard will be available at [http://127.0.0.1:8000](http://127.0.0.1:8000).

> **Note on First Run**: If you do not specify a `DEFAULT_ADMIN_PASSWORD` in your `.env` file, the system will securely generate a random password for the `admin` user on the first start and print it directly to the console output. Look for it in your terminal logs!

## Production Deployment

Detailed deployment guides are available in the `docs/` folder:

- [Production Deployment with Nginx (Linux)](docs/linux-deploy.md)
- [Detailed Windows Server Deployment Guide](docs/windows-server-deploy.md)
- [Simple Direct-IP Deployment (Linux)](docs/simple_start.md)

### Important Production Recommendations:
- Generate and set a strong, random `JWT_SECRET` in `.env` (e.g., using `python -c "import secrets; print(secrets.token_urlsafe(48))"`).
- Set `TRUSTED_HOSTS` to contain your actual domain name or server IP to prevent HTTP Host header spoofing attacks.
- If placing the application behind an SSL/TLS reverse proxy (e.g., Nginx + Let's Encrypt), make sure to configure `SESSION_HTTPS_ONLY=true` and `FORCE_HTTPS=true` in your `.env`.
