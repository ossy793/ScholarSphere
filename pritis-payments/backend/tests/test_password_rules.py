import pytest
from httpx import AsyncClient

from app.schemas.auth import check_password_strength
from tests.conftest import signup_body


@pytest.mark.parametrize("password", ["Correct-horse 1", "Abc123!x", "P@ssw0rd", "Lagos_2026x"])
def test_strong_passwords_pass(password: str) -> None:
    assert check_password_strength(password) == password


@pytest.mark.parametrize(
    ("password", "missing"),
    [
        ("lowercase1!", "an uppercase letter"),
        ("UPPERCASE1!", "a lowercase letter"),
        ("NoNumbers!", "a number"),
        ("NoSymbols1", "a symbol such as ! @ # $ or -"),
        ("Spaces Only 1", "a symbol such as ! @ # $ or -"),  # a space is not a symbol
    ],
)
def test_each_rule_is_enforced(password: str, missing: str) -> None:
    with pytest.raises(ValueError) as exc:
        check_password_strength(password)
    assert str(exc.value) == f"Password must include {missing}"


def test_all_missing_rules_are_listed() -> None:
    with pytest.raises(ValueError) as exc:
        check_password_strength("abcdefgh")
    assert str(exc.value) == (
        "Password must include an uppercase letter, a number and a symbol such as ! @ # $ or -"
    )


async def test_signup_refuses_a_weak_password(client: AsyncClient) -> None:
    body = await signup_body(password="weakpassword")
    resp = await client.post("/api/user/auth/signup", json=body)
    assert resp.status_code == 422
    detail = resp.json()["error"]["details"][0]
    assert detail["field"] == "body.password"
    assert detail["message"].startswith("Password must include an uppercase letter")


async def test_login_does_not_apply_the_rules(client: AsyncClient) -> None:
    # Someone with an older, simpler password must still be able to log in (and get a
    # normal "incorrect" answer rather than a validation error).
    resp = await client.post(
        "/api/user/auth/login", json={"email": "ada@example.com", "password": "simple"}
    )
    assert resp.status_code == 401
