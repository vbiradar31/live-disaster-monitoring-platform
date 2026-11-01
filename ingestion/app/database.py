"""Create the shared SQLAlchemy engine and request-scoped database sessions."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings


engine = create_engine(
    settings.sqlalchemy_url(),
    echo=True,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False
)

class Base(DeclarativeBase):
    pass

def get_db():
    """Yield one session per request and close it after the request completes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
