from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}
    assert resp.headers["X-Request-ID"]


async def test_request_id_is_echoed(client: AsyncClient) -> None:
    resp = await client.get("/api/health", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"
