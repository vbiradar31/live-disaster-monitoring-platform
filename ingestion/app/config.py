"""Resolve ingestion database settings for local development and containers.

Environment variables override the repository .env file. The .env lookup keeps
host development compatible; containers receive settings through their runtime
environment and do not need a copied .env file.
"""

import os
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from pydantic_settings import BaseSettings, SettingsConfigDict


CONFIG_PATH = Path(__file__).resolve()
ROOT_DIR = CONFIG_PATH.parents[2]


class Settings(BaseSettings):
    """Database settings with legacy URL and structured credential support."""

    database_url: str | None = None
    postgres_user: str | None = None
    postgres_password: str | None = None
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str | None = None

    model_config = SettingsConfigDict(
        # Integration tests set this flag so configuration never opens the
        # developer's .env while targeting a disposable database.
        env_file=None if os.getenv("LDMP_DISABLE_DOTENV") == "1" else ROOT_DIR / ".env",
        extra="ignore",
    )

    def sqlalchemy_url(self) -> URL:
        """Resolve the SQLAlchemy URL, preferring DATABASE_URL when configured.

        Separate PostgreSQL fields are combined with URL.create() so reserved
        characters in credentials are handled as values, not URL syntax.
        """
        if self.database_url:
            return make_url(self.database_url)

        missing = [
            name
            for name, value in (
                ("POSTGRES_USER", self.postgres_user),
                ("POSTGRES_PASSWORD", self.postgres_password),
                ("POSTGRES_DB", self.postgres_db),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Database configuration is missing. Set DATABASE_URL or provide "
                + ", ".join(missing)
                + "."
            )

        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


settings = Settings()
