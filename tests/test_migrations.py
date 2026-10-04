import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

import models  # noqa: F401  (registers the tables on the metadata)
from db import metadata
from tests.conftest import alembic_config

BEFORE_NUMERIC = "503a98bf8050"
BEFORE_LOWERCASE = "84c70b91eeac"


def test_models_match_migrations(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []


def insert_legacy_complaint(engine, amount):
    with engine.begin() as conn:
        user_id = conn.execute(
            sa.text(
                "INSERT INTO users (email, password, role) "
                "VALUES ('old@example.com', 'x', 'complainer') RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO complaints (title, description, photo_url, amount,"
                " complainer_id) VALUES ('old', 'd', 'https://b/old.png', :amount, :id)"
            ),
            {"amount": amount, "id": user_id},
        )


def test_numeric_migration_with_legacy_amounts(engine):
    cfg = alembic_config()
    command.downgrade(cfg, BEFORE_NUMERIC)
    try:
        insert_legacy_complaint(engine, 12.345)
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            amount = conn.execute(sa.text("SELECT amount FROM complaints")).scalar_one()
        assert str(amount) == "12.35"
    finally:
        command.upgrade(cfg, "head")


@pytest.mark.parametrize("amount", [123456789.5, float("inf"), float("nan")])
def test_numeric_migration_refuses_amounts_that_do_not_fit(engine, amount):
    cfg = alembic_config()
    command.downgrade(cfg, BEFORE_NUMERIC)
    try:
        insert_legacy_complaint(engine, amount)
        with pytest.raises(RuntimeError, match="do not fit NUMERIC"):
            command.upgrade(cfg, "head")
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM complaints"))
        command.upgrade(cfg, "head")


def insert_legacy_users(engine, *emails):
    with engine.begin() as conn:
        for email in emails:
            conn.execute(
                sa.text(
                    "INSERT INTO users (email, password, role) "
                    "VALUES (:email, 'x', 'complainer')"
                ),
                {"email": email},
            )


def test_email_migration_lowercases(engine):
    cfg = alembic_config()
    command.downgrade(cfg, BEFORE_LOWERCASE)
    try:
        insert_legacy_users(engine, "Jane.Doe@Example.COM", "bob@example.com")
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            emails = conn.execute(sa.text("SELECT email FROM users ORDER BY id")).all()
        assert [row.email for row in emails] == [
            "jane.doe@example.com",
            "bob@example.com",
        ]
    finally:
        command.upgrade(cfg, "head")


def test_email_migration_refuses_case_duplicates(engine):
    cfg = alembic_config()
    command.downgrade(cfg, BEFORE_LOWERCASE)
    try:
        insert_legacy_users(engine, "Jane@example.com", "jane@example.com")
        with pytest.raises(RuntimeError, match="jane@example.com .user ids 1, 2."):
            command.upgrade(cfg, "head")
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM users"))
        command.upgrade(cfg, "head")
