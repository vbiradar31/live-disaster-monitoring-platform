"""Add providers, ingestion runs, observations, and correlation decisions.

Revision ID: 9f3b7e21a6c4
Revises: 36eab4f3c6a6
Create Date: 2026-10-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f3b7e21a6c4"
down_revision: Union[str, Sequence[str], None] = "36eab4f3c6a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_providers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider_id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("records_received", sa.Integer(), nullable=False),
        sa.Column("records_inserted", sa.Integer(), nullable=False),
        sa.Column("records_duplicate", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["provider_id"], ["source_providers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingestion_runs_provider_id", "ingestion_runs", ["provider_id"])
    op.create_table(
        "source_observations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider_id", sa.UUID(), nullable=False),
        sa.Column("provider_event_id", sa.String(length=250), nullable=False),
        sa.Column("ingestion_run_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=True),
        sa.Column("origin_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("magnitude", sa.Float(), nullable=True),
        sa.Column("depth_km", sa.Float(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("match_status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["source_providers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "provider_event_id", name="uq_observation_provider_event"),
    )
    op.create_index("ix_source_observations_provider_id", "source_observations", ["provider_id"])
    op.create_index("ix_source_observations_ingestion_run_id", "source_observations", ["ingestion_run_id"])
    op.create_index("ix_source_observations_event_id", "source_observations", ["event_id"])
    op.create_index("ix_source_observations_origin_time", "source_observations", ["origin_time"])
    op.create_table(
        "correlation_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("observation_id", sa.UUID(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("selected_event_id", sa.UUID(), nullable=True),
        sa.Column("candidate_event_ids", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("time_delta_seconds", sa.Float(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["observation_id"], ["source_observations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_event_id"], ["events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("observation_id"),
    )


def downgrade() -> None:
    op.drop_table("correlation_decisions")
    op.drop_index("ix_source_observations_origin_time", table_name="source_observations")
    op.drop_index("ix_source_observations_event_id", table_name="source_observations")
    op.drop_index("ix_source_observations_ingestion_run_id", table_name="source_observations")
    op.drop_index("ix_source_observations_provider_id", table_name="source_observations")
    op.drop_table("source_observations")
    op.drop_index("ix_ingestion_runs_provider_id", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_table("source_providers")
