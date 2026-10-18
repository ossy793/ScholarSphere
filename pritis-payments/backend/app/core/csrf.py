import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.cookies import CSRF_COOKIE, CSRF_HEADER
from app.core.errors import error_response

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Paystack can't send our CSRF header; webhooks are authenticated by signature instead.
EXEMPT_PREFIXES = ("/api/webhooks/",)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit check: state-changing API calls must echo the csrf_token cookie in a header."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if (
            request.method not in SAFE_METHODS
            and path.startswith("/api/")
            and not path.startswith(EXEMPT_PREFIXES)
        ):
            cookie = request.cookies.get(CSRF_COOKIE)
            header = request.headers.get(CSRF_HEADER)
            if not cookie or not header or not hmac.compare_digest(cookie, header):
                return error_response(
                    403, "csrf_failed", "Security check failed. Refresh the page and try again."
                )
        return await call_next(request)
