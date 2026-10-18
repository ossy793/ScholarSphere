"""The one error shape every endpoint returns:

    {"error": {"code": "machine_code", "message": "Human text", "details": [...]}}
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "too_many_requests",
    503: "service_unavailable",
}


class AppError(Exception):
    """Raise from services and routes for an expected, client-facing error."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


def field_error(field: str, message: str, type_: str = "invalid") -> AppError:
    """A 422 in the same shape as Pydantic's, for checks that need the database or Paystack."""
    return AppError(
        "validation_error", "Some fields are invalid", 422,
        [{"field": f"body.{field}", "message": message, "type": type_}],
    )


def error_response(
    status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or []}},
    )


async def _app_error(_: Request, exc: AppError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").capitalize()
    return error_response(exc.status_code, code, message)


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "field": ".".join(str(p) for p in err["loc"]),
            # Our own validators raise ValueError; Pydantic prefixes those messages.
            "message": err["msg"].removeprefix("Value error, "),
            "type": err["type"],
        }
        for err in exc.errors()
    ]
    return error_response(422, "validation_error", "Some fields are invalid", details)


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "internal_error", "Something went wrong")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled_error)
