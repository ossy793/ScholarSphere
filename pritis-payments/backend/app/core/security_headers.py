"""Security headers on every response (design section 9). In production Nginx serves the
static pages and adds the same headers there (deploy/nginx/security-headers.conf); this
covers the API, and the pages when the API serves them locally.

The pages load scripts and styles only from their own origin (no inline code anywhere),
plus Google Fonts. Paystack checkout and Google sign-in are full-page redirects, which a
Content Security Policy doesn't restrict.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",  # data: for the authenticator QR code
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

BASE_HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "cross-origin-opener-policy": "same-origin",
}
HSTS = "max-age=31536000; includeSubDomains"
# The interactive API docs (local only) load Swagger from a CDN; they keep no CSP.
DOCS_PATHS = ("/api/docs", "/api/openapi.json")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        is_docs = path.startswith(DOCS_PATHS)
        is_api = path.startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                extra = {} if is_docs else dict(BASE_HEADERS)
                if self.hsts:
                    extra["strict-transport-security"] = HSTS
                if is_api and not is_docs:
                    # API answers carry personal data: never cache them anywhere.
                    extra.setdefault("cache-control", "no-store")
                for name, value in extra.items():
                    if name.encode() not in present:
                        headers.append((name.encode(), value.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
