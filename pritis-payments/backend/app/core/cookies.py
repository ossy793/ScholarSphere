"""Session cookies. All are host-only (no Domain), so dpp.* and admin.dpp.* never share them."""

from dataclasses import dataclass

from starlette.responses import Response

from app.core.config import get_settings

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"


@dataclass(frozen=True)
class SessionCookies:
    access: str
    refresh: str
    # The refresh cookie is only sent to the refresh and logout routes.
    refresh_path: str


USER_COOKIES = SessionCookies(access="dpp_access", refresh="dpp_refresh", refresh_path="/api/user/auth")
ADMIN_COOKIES = SessionCookies(access="dpa_access", refresh="dpa_refresh", refresh_path="/api/admin/auth")
# Short-lived proof that the password step passed; the authenticator code step needs it.
ADMIN_MFA_COOKIE = "dpa_mfa"


def set_session_cookies(
    response: Response, names: SessionCookies, access: str, refresh: str, refresh_max_age: int
) -> None:
    settings = get_settings()
    common = {"httponly": True, "secure": settings.cookie_secure, "samesite": "lax"}
    response.set_cookie(
        names.access, access, max_age=settings.access_token_minutes * 60, path="/api", **common
    )
    response.set_cookie(
        names.refresh, refresh, max_age=refresh_max_age, path=names.refresh_path, **common
    )


def clear_session_cookies(response: Response, names: SessionCookies) -> None:
    settings = get_settings()
    common = {"httponly": True, "secure": settings.cookie_secure, "samesite": "lax"}
    response.delete_cookie(names.access, path="/api", **common)
    response.delete_cookie(names.refresh, path=names.refresh_path, **common)


def set_csrf_cookie(response: Response, token: str) -> None:
    # Readable by JavaScript on purpose: the page copies it into the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE, token, path="/", httponly=False,
        secure=get_settings().cookie_secure, samesite="lax",
    )
