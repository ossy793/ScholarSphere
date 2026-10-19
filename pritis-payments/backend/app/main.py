from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import BACKEND_DIR, get_settings
from app.core.csrf import CSRFMiddleware
from app.core.errors import register_error_handlers
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.core.security_headers import SecurityHeadersMiddleware
from app.routers import admin, health, user, webhooks

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    docs = settings.docs_enabled
    app = FastAPI(
        title="Pritis DPP",
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )

    # Last added runs first: logging wraps everything, then security headers, then CSRF,
    # then the session.
    # The signed session cookie only carries Google sign-in state for a few minutes.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.jwt_secret,
        session_cookie="dpp_oauth",
        max_age=600,
        path="/api/user/auth",
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)
    app.add_middleware(RequestLoggingMiddleware)
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(user.router)
    app.include_router(admin.router)
    app.include_router(webhooks.router)

    # In production Nginx serves the static pages; locally the API serves them, so the
    # browser still calls /api on its own origin. http://localhost:8100 is the user site and
    # http://admin.localhost:8100 the admin site, with separate cookies like in production.
    if settings.environment == "local":
        app.mount("/shared", StaticFiles(directory=FRONTEND_DIR / "shared"), name="shared")
        app.mount(
            "/",
            SiteByHost(
                user=StaticFiles(directory=FRONTEND_DIR / "user", html=True),
                admin=StaticFiles(directory=FRONTEND_DIR / "admin", html=True),
            ),
            name="sites",
        )
    return app


class SiteByHost:
    """Local development only: picks the admin or user pages by the Host header."""

    def __init__(self, user: ASGIApp, admin: ASGIApp) -> None:
        self.user = user
        self.admin = admin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        host = dict(scope.get("headers") or []).get(b"host", b"").decode()
        site = self.admin if host.startswith("admin.") else self.user
        await site(scope, receive, send)


app = create_app()
