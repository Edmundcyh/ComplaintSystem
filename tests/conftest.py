import asyncio
import base64
import itertools
import os

# Tests empty the tables, so they must never use the database from .env.
# Environment variables take precedence over .env for python-decouple.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/complaints_test",
)
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient

from db import DATABASE_URL
from main import app
from services.s3 import get_s3_service
from services.ses import get_ses_service
from services.wise import get_wise_service

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
VALID_IBAN = "DE89370400440532013000"


def alembic_config():
    cfg = Config(os.path.join(ROOT_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT_DIR, "migrations"))
    return cfg


def truncate(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text("TRUNCATE transactions, complaints, users RESTART IDENTITY CASCADE")
        )


@pytest.fixture(scope="session")
def engine():
    engine = sa.create_engine(DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_db(engine):
    # Downgrade to empty and upgrade again so every migration is exercised
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    truncate(engine)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def clean_db(engine):
    truncate(engine)


@pytest.fixture(scope="session")
def client(migrated_db):
    with TestClient(app) as client:
        yield client


class FakeS3:
    def __init__(self):
        self.uploaded = {}
        self.deleted = []

    async def upload_photo(self, data, key, content_type):
        self.uploaded[key] = (data, content_type)
        return f"https://bucket.s3.eu-west-1.amazonaws.com/{key}"

    async def delete_photo(self, key):
        self.deleted.append(key)

    def presigned_url(self, photo_url):
        return photo_url + "?signed"


class FakeWise:
    def __init__(self):
        self._ids = itertools.count(3_000_000_000)  # bigger than a 32-bit int
        self.quotes = []
        self.recipients = []
        self.funded = []
        self.cancelled = []
        self.fail = set()  # names of methods that should fail
        self.transfer_id_override = None
        self.fund_delay = 0

    def _maybe_fail(self, name):
        if name in self.fail:
            raise HTTPException(502, "Payment provider is not available at the moment")

    async def create_quote(self, amount):
        self._maybe_fail("create_quote")
        self.quotes.append(amount)
        return "quote-uuid"

    async def create_recipient_account(self, full_name, iban):
        self._maybe_fail("create_recipient_account")
        self.recipients.append((full_name, iban))
        return 42

    async def create_transfer(self, target_account_id, quote_id):
        self._maybe_fail("create_transfer")
        if self.transfer_id_override is not None:
            return self.transfer_id_override
        return next(self._ids)

    async def fund_transfer(self, transfer_id):
        if self.fund_delay:
            await asyncio.sleep(self.fund_delay)
        self._maybe_fail("fund_transfer")
        self.funded.append(transfer_id)

    async def cancel_transfer(self, transfer_id):
        self._maybe_fail("cancel_transfer")
        self.cancelled.append(transfer_id)


class FakeSES:
    def __init__(self):
        self.sent = []
        self.fail = False

    async def send_mail(self, subject, to_addresses, text_data):
        if self.fail:
            raise RuntimeError("SES is down")
        self.sent.append((subject, to_addresses))


class Fakes:
    def __init__(self):
        self.s3 = FakeS3()
        self.wise = FakeWise()
        self.ses = FakeSES()


@pytest.fixture(autouse=True)
def fakes():
    fakes = Fakes()
    app.dependency_overrides[get_s3_service] = lambda: fakes.s3
    app.dependency_overrides[get_wise_service] = lambda: fakes.wise
    app.dependency_overrides[get_ses_service] = lambda: fakes.ses
    yield fakes
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(client, engine):
    counter = itertools.count(1)

    def make_user(role="complainer", **overrides):
        data = {
            "email": f"user{next(counter)}@example.com",
            "password": "password123",
            "phone": "+49 123",
            "first_name": "Jane",
            "last_name": "Doe",
            "iban": VALID_IBAN,
            **overrides,
        }
        resp = client.post("/register/", json=data)
        assert resp.status_code == 201, resp.text
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE users SET role = :role WHERE email = :email"),
                {"role": role, "email": data["email"]},
            )
            user_id = conn.execute(
                sa.text("SELECT id FROM users WHERE email = :email"),
                {"email": data["email"]},
            ).scalar_one()
        headers = {"Authorization": f"Bearer {resp.json()['token']}"}
        return {"id": user_id, "email": data["email"], "headers": headers}

    return make_user


def complaint_body(**overrides):
    return {
        "title": "Broken phone",
        "description": "Arrived broken",
        "amount": 12.5,
        "encoded_photo": base64.b64encode(PNG).decode(),
        "extension": "png",
        **overrides,
    }


@pytest.fixture
def create_complaint(client):
    def create_complaint(user, **overrides):
        resp = client.post(
            "/complaints/", json=complaint_body(**overrides), headers=user["headers"]
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    return create_complaint
