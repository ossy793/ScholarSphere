import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.models import University, User
from app.schemas.auth import normalize_ng_phone
from tests.conftest import signup, signup_body, university_id


@pytest.mark.parametrize(
    "raw",
    ["08031234567", "0803 123 4567", "803-123-4567", "2348031234567", "+234 803 123 4567",
     "07012345678", "09112345678", "0813(123)4567"],
)
def test_nigerian_phone_numbers_are_normalised(raw: str) -> None:
    assert normalize_ng_phone(raw).startswith("+234")
    assert len(normalize_ng_phone(raw)) == 14


@pytest.mark.parametrize(
    "raw", ["12345", "0803123456", "080312345678", "06031234567", "+447911123456", "08231234567"]
)
def test_invalid_phone_numbers_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_ng_phone(raw)


async def test_universities_list_is_public_and_sorted(raw_client: AsyncClient) -> None:
    resp = await raw_client.get("/api/user/universities")
    assert resp.status_code == 200
    names = [u["name"] for u in resp.json()]
    assert len(names) > 200
    assert names == sorted(names, key=str.casefold)  # alphabetical, ignoring case
    assert "University of Lagos" in names
    unilag = next(u for u in resp.json() if u["name"] == "University of Lagos")
    assert unilag["short_name"] == "UNILAG"


async def test_inactive_university_is_hidden_and_refused(client: AsyncClient) -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(University).where(University.name == "University of Lagos").values(is_active=False)
        )
        await db.commit()
    try:
        names = [u["name"] for u in (await client.get("/api/user/universities")).json()]
        assert "University of Lagos" not in names
        resp = await client.post("/api/user/auth/signup", json=await signup_body())
        assert resp.status_code == 422
        assert resp.json()["error"]["details"][0]["field"] == "body.university_id"
    finally:
        async with SessionLocal() as db:
            await db.execute(
                update(University).where(University.name == "University of Lagos").values(is_active=True)
            )
            await db.commit()


async def test_signup_stores_profile(client: AsyncClient) -> None:
    me = (await signup(client)).json()
    assert me["phone"] == "+2348031234567"
    assert me["university"]["name"] == "University of Lagos"
    assert me["level"] == "200"
    assert me["profile_complete"] is True


async def test_unknown_university_is_refused(client: AsyncClient) -> None:
    body = await signup_body()
    body["university_id"] = "00000000-0000-0000-0000-000000000000"
    resp = await client.post("/api/user/auth/signup", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["message"] == "Choose your university from the list"


async def test_complete_profile_for_google_style_account(client: AsyncClient) -> None:
    await signup(client)
    # Make it look like a fresh Google sign-up with no profile yet.
    async with SessionLocal() as db:
        await db.execute(
            update(User).values(phone=None, university_id=None, level=None, email_verified_at=utcnow())
        )
        await db.commit()
    assert (await client.get("/api/user/me")).json()["profile_complete"] is False

    resp = await client.put(
        "/api/user/me/profile",
        json={"phone": "+234 905 555 0000", "university_id": await university_id("Covenant University, Ota"),
              "level": "400", "full_name": "Ada N. Obi"},
    )
    assert resp.status_code == 200
    me = resp.json()
    assert me["profile_complete"] is True
    assert me["phone"] == "+2349055550000"
    assert me["university"]["name"] == "Covenant University, Ota"
    assert me["level"] == "400"
    assert me["full_name"] == "Ada N. Obi"


async def test_profile_update_requires_login(client: AsyncClient) -> None:
    resp = await client.put(
        "/api/user/me/profile",
        json={"phone": "08031234567", "university_id": await university_id(), "level": "100"},
    )
    assert resp.status_code == 401
