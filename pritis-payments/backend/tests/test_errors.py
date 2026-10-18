from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.errors import AppError, register_error_handlers


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    class Body(BaseModel):
        amount_kobo: int

    @app.post("/validate")
    async def validate(body: Body) -> dict:
        return {}

    @app.get("/app-error")
    async def app_error() -> dict:
        raise AppError("due_closed", "This due is closed", 409, [{"field": "due_id"}])

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("secret internals")

    return app


async def _client() -> AsyncClient:
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_not_found_uses_error_shape(client: AsyncClient) -> None:
    resp = await client.get("/api/nope")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "Not Found", "details": []}}


async def test_validation_error_lists_fields() -> None:
    async with await _client() as c:
        resp = await c.post("/validate", json={"amount_kobo": "lots"})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"][0]["field"] == "body.amount_kobo"


async def test_app_error() -> None:
    async with await _client() as c:
        resp = await c.get("/app-error")
    assert resp.status_code == 409
    assert resp.json() == {
        "error": {"code": "due_closed", "message": "This due is closed", "details": [{"field": "due_id"}]}
    }


async def test_unhandled_error_hides_internals() -> None:
    async with await _client() as c:
        resp = await c.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "secret" not in resp.text
