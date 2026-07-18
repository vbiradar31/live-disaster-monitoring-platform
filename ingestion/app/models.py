from datetime import datetime, UTC
from uuid import UUID, uuid7

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )

    external_id: Mapped[str] = mapped_column(
        String(200),
        unique=True,
        index=True,
        nullable=False,
    )

    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    status: Mapped[str | None] = mapped_column(
        String(50),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    geometries: Mapped[list["EventGeometry"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
    )

    episodes: Mapped[list["EventEpisode"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventEpisode.episode_number",
    )


class EventGeometry(Base):
    __tablename__ = "event_geometries"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    geometry_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    geometry_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    event: Mapped["Event"] = relationship(
        back_populates="geometries"
    )


class EventEpisode(Base):
    __tablename__ = "event_episodes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    episode_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    title: Mapped[str | None] = mapped_column(
        String(200),
    )

    description: Mapped[str | None] = mapped_column(
        Text,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    event: Mapped["Event"] = relationship(
        back_populates="episodes"
    )