from pathlib import Path
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


CONFIG_PATH = Path(__file__).resolve()
ROOT_DIR = next(
    (parent for parent in CONFIG_PATH.parents if (parent / "alembic.ini").is_file()),
    CONFIG_PATH.parents[2],
)


class Settings(BaseSettings):
    database_url: str

    model_config = SettingsConfigDict(
        # Integration tests set this flag so configuration never opens the
        # developer's .env while targeting a disposable database.
        env_file=None if os.getenv("LDMP_DISABLE_DOTENV") == "1" else ROOT_DIR / ".env",
        extra="ignore"
    )

settings = Settings()
