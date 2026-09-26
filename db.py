from urllib.parse import quote

import databases
import sqlalchemy
from decouple import config


def _database_url():
    url = config("DATABASE_URL", default="")
    if url.startswith("postgres://"):
        # Heroku-style scheme: asyncpg accepts it, SQLAlchemy/Alembic don't
        url = "postgresql://" + url[len("postgres://") :]
    if url:
        return url
    # Quoted so passwords with characters like @ : / % work
    user = quote(config("DB_USER"), safe="")
    password = quote(config("DB_PASSWORD"), safe="")
    host = config("DB_HOST", default="localhost")
    port = config("DB_PORT", default="5432")
    name = config("DB_NAME", default="complaints")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


DATABASE_URL = _database_url()
database = databases.Database(DATABASE_URL)
metadata = sqlalchemy.MetaData()
