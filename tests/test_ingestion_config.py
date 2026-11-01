import pytest
from sqlalchemy.engine import URL

from app.config import Settings


def test_database_url_setting_remains_supported():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://local:secret@localhost:5432/disaster_db",
    )

    assert settings.sqlalchemy_url() == URL.create(
        "postgresql+psycopg",
        username="local",
        password="secret",
        host="localhost",
        port=5432,
        database="disaster_db",
    )


def test_postgres_fields_create_url_safely():
    settings = Settings(
        _env_file=None,
        postgres_user="local@user",
        postgres_password="p@ss:/word",
        postgres_host="postgres",
        postgres_db="disaster_db",
    )

    url = settings.sqlalchemy_url()

    assert url.username == "local@user"
    assert url.password == "p@ss:/word"
    assert url.host == "postgres"
    assert url.database == "disaster_db"


def test_missing_database_configuration_has_clear_error():
    settings = Settings(_env_file=None)

    with pytest.raises(ValueError, match="Set DATABASE_URL or provide POSTGRES_USER"):
        settings.sqlalchemy_url()
