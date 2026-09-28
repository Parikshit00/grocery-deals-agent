"""baseline: leaflets + scan_runs

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leaflets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("chain", sa.Text, nullable=False),
        sa.Column("edition", sa.Text, nullable=False),
        sa.Column("leaflet_key", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_to", sa.Date, nullable=False),
        sa.Column("dates_source", sa.Text, nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("pdf_url", sa.Text),
        sa.Column("page_urls", pg.JSONB),
        sa.Column("tier", sa.SmallInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("page_count", sa.Integer),
        sa.Column("pages_done", pg.ARRAY(sa.Integer), nullable=False, server_default="{}"),
        sa.Column("offers", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("embeddings", sa.LargeBinary),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("scan_run_id", pg.UUID(as_uuid=True)),
        sa.UniqueConstraint("chain", "edition", "leaflet_key", name="uq_leaflets_key"),
    )
    op.create_index(
        "ix_leaflets_chain_edition_valid_to", "leaflets", ["chain", "edition", "valid_to"]
    )

    op.create_table(
        "scan_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("scan_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("chain", sa.Text, nullable=False),
        sa.Column("plz", sa.Text),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("store", pg.JSONB),
        sa.Column("stores_nearby", pg.JSONB),
        sa.Column("tier", sa.SmallInteger),
        sa.Column("leaflet_ids", pg.JSONB),
        sa.Column("next_week", sa.Text),
        sa.Column("pages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("offers", sa.Integer, nullable=False, server_default="0"),
        sa.Column("flagged", sa.Integer, nullable=False, server_default="0"),
        sa.Column("self_heal", pg.JSONB),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_scan_runs_chain_plz_finished", "scan_runs", ["chain", "plz", "finished_at"]
    )


def downgrade() -> None:
    op.drop_table("scan_runs")
    op.drop_table("leaflets")
