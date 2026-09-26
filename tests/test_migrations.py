from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

import models  # noqa: F401  (registers the tables on the metadata)
from db import metadata


def test_models_match_migrations(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []
