"""
Main FastAPI application entry point.
"""
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import auth_router, config_router, export_router, providers_router, status_router
from app.api.auth import get_current_user, get_or_create_csrf_token
from app.config import (
    APP_HOST,
    APP_PORT,
    AUTO_CREATE_ADMIN,
    DEBUG,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USERNAME,
    DEFAULT_JWT_SECRET,
    FORCE_HTTPS,
    JWT_SECRET,
    LOG_FORMAT,
    LOG_LEVEL,
    SESSION_HTTPS_ONLY,
    TRUSTED_HOSTS,
)
from app.models import init_db
from app.scheduler import start_scheduler, stop_scheduler

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format=LOG_FORMAT
)
logger = logging.getLogger(__name__)


def log_startup_warnings() -> None:
    """Emit actionable warnings for unsafe production settings."""
    if JWT_SECRET == DEFAULT_JWT_SECRET:
        logger.warning("JWT_SECRET uses the default value. Replace it before production deployment.")

    if AUTO_CREATE_ADMIN and not DEFAULT_ADMIN_PASSWORD:
        logger.warning(
            "AUTO_CREATE_ADMIN is enabled but DEFAULT_ADMIN_PASSWORD is empty. "
            "A random temporary password will be generated at first start."
        )

    if TRUSTED_HOSTS == ["*"]:
        logger.warning("TRUSTED_HOSTS allows any host. Set a fixed list in production.")

    if not SESSION_HTTPS_ONLY:
        logger.warning("SESSION_HTTPS_ONLY is disabled. Enable it behind HTTPS in production.")

    if APP_HOST in {"127.0.0.1", "localhost"}:
        logger.warning("APP_HOST is bound to loopback only. The service will not be reachable from other machines.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting up...")

    await init_db()
    logger.info("Database initialized")

    log_startup_warnings()
    await create_admin_user()

    start_scheduler()

    yield

    logger.info("Shutting down...")
    stop_scheduler()


app = FastAPI(
    title="Network Monitor",
    description="Production-ready network monitoring system",
    version="1.1.0",
    lifespan=lifespan
)

if TRUSTED_HOSTS and TRUSTED_HOSTS != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)

if FORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)

app.add_middleware(
    SessionMiddleware,
    secret_key=JWT_SECRET,
    max_age=86400,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

app.include_router(auth_router)
app.include_router(config_router)
app.include_router(providers_router)
app.include_router(status_router)
app.include_router(export_router)


def build_template_context(request: Request, user, **extra) -> dict:
    """Build a consistent template context with CSRF token."""
    context = {
        "request": request,
        "user": user,
        "csrf_token": get_or_create_csrf_token(request),
    }
    context.update(extra)
    return context


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(get_current_user)):
    """Dashboard page."""
    return templates.TemplateResponse("dashboard.html", build_template_context(request, user))


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    error = request.query_params.get("error")
    return templates.TemplateResponse("login.html", build_template_context(request, None, error=error))


@app.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request, user=Depends(get_current_user)):
    """Providers management page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("providers.html", build_template_context(request, user))


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, user=Depends(get_current_user)):
    """History page."""
    return templates.TemplateResponse("history.html", build_template_context(request, user))


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, user=Depends(get_current_user)):
    """Configuration page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("config.html", build_template_context(request, user))


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    from app.scheduler import get_scheduler_status

    warnings = []
    if JWT_SECRET == DEFAULT_JWT_SECRET:
        warnings.append("default_jwt_secret")
    if TRUSTED_HOSTS == ["*"]:
        warnings.append("trusted_hosts_wildcard")
    if not SESSION_HTTPS_ONLY:
        warnings.append("session_cookie_not_https_only")
    if APP_HOST in {"127.0.0.1", "localhost"}:
        warnings.append("loopback_bind_only")

    return {
        "status": "healthy",
        "scheduler": get_scheduler_status(),
        "warnings": warnings,
    }


async def create_admin_user():
    """Create the initial admin user when explicitly enabled."""
    from sqlalchemy import func, select

    from app.models import AsyncSessionLocal, User
    from app.services import get_password_hash

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.count(User.id)))
        user_count = result.scalar() or 0

        if user_count > 0:
            logger.info("Found %s existing user(s), skipping admin creation", user_count)
            return

        if not AUTO_CREATE_ADMIN:
            logger.warning(
                "No users found and AUTO_CREATE_ADMIN is disabled. "
                "Set AUTO_CREATE_ADMIN=true with DEFAULT_ADMIN_* variables for first start."
            )
            return

        generated_password = None
        admin_password = DEFAULT_ADMIN_PASSWORD
        if not admin_password:
            generated_password = secrets.token_urlsafe(12)
            admin_password = generated_password

        admin = User(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=get_password_hash(admin_password),
            is_active=1
        )
        session.add(admin)
        await session.commit()

        logger.warning("Created initial admin user '%s'", DEFAULT_ADMIN_USERNAME)
        if generated_password:
            logger.warning("Generated temporary admin password: %s", generated_password)
        else:
            logger.warning("Change DEFAULT_ADMIN_PASSWORD after the first login.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=APP_HOST,
        port=APP_PORT,
        reload=DEBUG,
        log_level=LOG_LEVEL.lower()
    )
