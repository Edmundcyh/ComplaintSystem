from datetime import datetime, timedelta, timezone

import jwt
import sqlalchemy as sa

from tests.conftest import VALID_IBAN

SECRET = "test-secret-key-that-is-long-enough-for-hs256"


def register(client, **overrides):
    data = {
        "email": "new@example.com",
        "password": "password123",
        "phone": "123",
        "first_name": "New",
        "last_name": "User",
        "iban": VALID_IBAN,
        **overrides,
    }
    return client.post("/register/", json=data)


def test_register_and_login(client, engine):
    resp = register(client, iban="de89 3704 0044 0532 0130 00")
    assert resp.status_code == 201
    assert "token" in resp.json()

    resp = client.post(
        "/login/", json={"email": "new@example.com", "password": "password123"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "complainer"
    with engine.begin() as conn:
        iban = conn.execute(sa.text("SELECT iban FROM users")).scalar_one()
    assert iban == "DE89370400440532013000"


def test_register_validation(client):
    assert register(client, email="not-an-email").status_code == 422
    assert register(client, password="short").status_code == 422
    assert register(client, iban="DE00370400440532013000").status_code == 422
    assert register(client, iban="nonsense").status_code == 422
    assert register(client, first_name="x" * 31).status_code == 422
    assert register(client, first_name="").status_code == 422


def test_register_cannot_choose_role(client, engine):
    assert register(client, role="admin").status_code == 201
    with engine.begin() as conn:
        role = conn.execute(sa.text("SELECT role FROM users")).scalar_one()
    assert role == "complainer"


def test_duplicate_email(client):
    assert register(client).status_code == 201
    resp = register(client)
    assert resp.status_code == 400


def test_wrong_credentials(client):
    register(client)
    for email, password in [
        ("new@example.com", "wrong-password"),
        ("nobody@example.com", "password123"),
    ]:
        resp = client.post("/login/", json={"email": email, "password": password})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Wrong email or password"


def test_long_password(client):
    # bcrypt uses the first 72 bytes; longer passwords must still work
    password = "ä" * 50  # 100 bytes in UTF-8
    assert register(client, password=password).status_code == 201
    resp = client.post(
        "/login/", json={"email": "new@example.com", "password": password}
    )
    assert resp.status_code == 200


def token(**payload):
    payload.setdefault("exp", datetime.now(timezone.utc) + timedelta(hours=1))
    return jwt.encode(payload, SECRET, algorithm="HS256")


def test_bad_tokens_get_401(client, make_user):
    user = make_user()
    expired = token(sub=str(user["id"]), exp=datetime.now(timezone.utc) - timedelta(1))
    cases = {
        "garbage": "not-a-jwt",
        "wrong key": jwt.encode(
            {"sub": str(user["id"]), "exp": datetime.now(timezone.utc) + timedelta(1)},
            "another-secret-key-that-is-long-enough",
            algorithm="HS256",
        ),
        "expired": expired,
        "no sub": token(),
        "integer sub (issued before the upgrade)": token(sub=user["id"]),
        "non-numeric sub": token(sub="abc"),
        "deleted user": token(sub="999"),
    }
    for name, value in cases.items():
        resp = client.get("/complaints/", headers={"Authorization": f"Bearer {value}"})
        assert resp.status_code == 401, name
    resp = client.get("/complaints/", headers={"Authorization": f"Bearer {expired}"})
    assert resp.json()["detail"] == "Token is expired"


def test_numbers_accepted_for_text_fields(client, engine):
    # pydantic v1 turned numbers into strings; keep accepting them
    assert register(client, phone=359888123456, password=12345678).status_code == 201
    resp = client.post(
        "/login/", json={"email": "new@example.com", "password": 12345678}
    )
    assert resp.status_code == 200
    with engine.begin() as conn:
        phone = conn.execute(sa.text("SELECT phone FROM users")).scalar_one()
    assert phone == "359888123456"


def test_validation_errors_do_not_echo_passwords(client):
    resp = register(client, password="Pw9#xyz")
    assert resp.status_code == 422
    assert "Pw9#xyz" not in resp.text
