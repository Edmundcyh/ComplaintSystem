import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

import models  # noqa: F401  (registers the tables on the metadata)
from db import metadata
from tests.conftest import alembic_config

BEFORE_NUMERIC = "503a98bf8050"


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
