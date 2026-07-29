"""Durable generation jobs and private local storage.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


campaign_language = postgresql.ENUM("fr", "en", name="campaignlanguage")
generation_stage = postgresql.ENUM(
    "analysis",
    "extraction",
    "art_direction",
    "background",
    "composition",
    "copy",
    "export",
    "packaging",
    name="generationstage",
)


def upgrade() -> None:
    bind = op.get_bind()
    campaign_language.create(bind, checkfirst=True)
    generation_stage.create(bind, checkfirst=True)

    op.alter_column(
        "products", "original_image_url", new_column_name="original_storage_key"
    )
    op.drop_column("products", "cutout_image_url")
    op.execute("UPDATE products SET category = 'autre' WHERE category IS NULL")
    op.alter_column(
        "products",
        "category",
        existing_type=sa.String(length=50),
        type_=sa.String(length=80),
        nullable=False,
    )
    op.add_column(
        "products",
        sa.Column(
            "original_mime",
            sa.String(50),
            nullable=False,
            server_default="image/jpeg",
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "original_sha256",
            sa.String(64),
            nullable=False,
            server_default="0000000000000000000000000000000000000000000000000000000000000000",
        ),
    )
    op.add_column(
        "products",
        sa.Column("original_width", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "products",
        sa.Column("original_height", sa.Integer(), nullable=False, server_default="1"),
    )
    for column in (
        "original_mime",
        "original_sha256",
        "original_width",
        "original_height",
    ):
        op.alter_column("products", column, server_default=None)

    op.drop_column("generations", "tone")
    op.drop_column("generations", "template")
    op.alter_column(
        "generations", "marketing_text", new_column_name="copy_json"
    )
    op.add_column(
        "generations",
        sa.Column(
            "source_generation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_generations_source_generation",
        "generations",
        "generations",
        ["source_generation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "generations",
        sa.Column(
            "language",
            campaign_language,
            nullable=False,
            server_default="fr",
        ),
    )
    op.add_column(
        "generations",
        sa.Column("seed", sa.BigInteger(), nullable=False, server_default="42"),
    )
    op.add_column(
        "generations",
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "generations",
        sa.Column(
            "input_snapshot_hash",
            sa.String(64),
            nullable=False,
            server_default="0000000000000000000000000000000000000000000000000000000000000000",
        ),
    )
    op.add_column(
        "generations",
        sa.Column("request_id", sa.String(200), nullable=True),
    )
    op.add_column(
        "generations",
        sa.Column("idempotency_key", sa.String(128), nullable=True),
    )
    op.execute(
        "UPDATE generations SET request_id = gen_random_uuid()::text, "
        "idempotency_key = 'legacy-' || id::text"
    )
    op.alter_column("generations", "request_id", nullable=False)
    op.alter_column("generations", "idempotency_key", nullable=False)
    op.add_column(
        "generations", sa.Column("stage", generation_stage, nullable=True)
    )
    op.add_column(
        "generations",
        sa.Column(
            "completed_stages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "generations",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("generations", sa.Column("claimed_by", sa.String(100)))
    op.add_column(
        "generations", sa.Column("claimed_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "generations", sa.Column("heartbeat_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "generations", sa.Column("next_attempt_at", sa.DateTime(timezone=True))
    )
    op.add_column("generations", sa.Column("remote_job_id", sa.String(200)))
    op.add_column("generations", sa.Column("runtime_id", sa.String(200)))
    op.add_column("generations", sa.Column("error_code", sa.String(80)))
    op.add_column(
        "generations",
        sa.Column("candidate_boxes", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.add_column(
        "generations",
        sa.Column("artifact_manifest", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.add_column(
        "generations", sa.Column("bundle_storage_key", sa.String(500))
    )
    op.add_column(
        "generations", sa.Column("bundle_checksum", sa.String(64))
    )
    op.add_column(
        "generations", sa.Column("started_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "generations", sa.Column("completed_at", sa.DateTime(timezone=True))
    )
    op.execute(
        sa.text(
            """
            UPDATE generations
            SET
                status = 'error',
                stage = NULL,
                completed_stages = '[]'::jsonb,
                attempt_count = 2,
                claimed_by = NULL,
                claimed_at = NULL,
                heartbeat_at = NULL,
                next_attempt_at = NULL,
                remote_job_id = NULL,
                runtime_id = NULL,
                error_code = 'ARTIFACT_CONTRACT_FAILED',
                error_message = 'Les fichiers générés sont incomplets ou invalides.',
                copy_json = NULL,
                candidate_boxes = NULL,
                artifact_manifest = NULL,
                bundle_storage_key = NULL,
                bundle_checksum = NULL,
                completed_at = COALESCE(completed_at, updated_at, created_at, now())
            """
        )
    )
    op.create_unique_constraint(
        "uq_generation_product_idempotency",
        "generations",
        ["product_id", "idempotency_key"],
    )
    op.create_index(
        "ix_generations_queue",
        "generations",
        ["status", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "ix_generations_cursor", "generations", ["created_at", "id"]
    )
    for column in (
        "language",
        "seed",
        "input_snapshot",
        "input_snapshot_hash",
    ):
        op.alter_column("generations", column, server_default=None)

    op.alter_column("assets", "url", new_column_name="storage_key")
    op.add_column("assets", sa.Column("artifact_name", sa.String(64)))
    op.execute(
        "UPDATE assets SET artifact_name = format::text || '.jpg'"
    )
    op.alter_column("assets", "artifact_name", nullable=False)
    op.alter_column(
        "assets",
        "format",
        existing_type=postgresql.ENUM(
            "instagram", "facebook", "linkedin", name="assetformat"
        ),
        nullable=True,
    )
    op.add_column(
        "assets",
        sa.Column("mime", sa.String(80), nullable=False, server_default="image/jpeg"),
    )
    op.add_column(
        "assets",
        sa.Column(
            "sha256",
            sa.String(64),
            nullable=False,
            server_default="0000000000000000000000000000000000000000000000000000000000000000",
        ),
    )
    op.add_column(
        "assets",
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column(
        "assets", "width", existing_type=sa.Integer(), nullable=True
    )
    op.alter_column(
        "assets", "height", existing_type=sa.Integer(), nullable=True
    )
    op.create_unique_constraint(
        "uq_asset_generation_name",
        "assets",
        ["generation_id", "artifact_name"],
    )
    for column in ("mime", "sha256", "byte_size"):
        op.alter_column("assets", column, server_default=None)


def downgrade() -> None:
    op.execute("DELETE FROM assets WHERE format IS NULL")
    op.alter_column(
        "assets",
        "format",
        existing_type=postgresql.ENUM(
            "instagram", "facebook", "linkedin", name="assetformat"
        ),
        nullable=False,
    )
    op.alter_column(
        "assets", "width", existing_type=sa.Integer(), nullable=False
    )
    op.alter_column(
        "assets", "height", existing_type=sa.Integer(), nullable=False
    )
    op.drop_constraint("uq_asset_generation_name", "assets", type_="unique")
    op.drop_column("assets", "byte_size")
    op.drop_column("assets", "sha256")
    op.drop_column("assets", "mime")
    op.drop_column("assets", "artifact_name")
    op.alter_column("assets", "storage_key", new_column_name="url")

    op.drop_index("ix_generations_cursor", table_name="generations")
    op.drop_index("ix_generations_queue", table_name="generations")
    op.drop_constraint(
        "uq_generation_product_idempotency", "generations", type_="unique"
    )
    for column in (
        "completed_at",
        "started_at",
        "bundle_checksum",
        "bundle_storage_key",
        "artifact_manifest",
        "candidate_boxes",
        "error_code",
        "runtime_id",
        "remote_job_id",
        "next_attempt_at",
        "heartbeat_at",
        "claimed_at",
        "claimed_by",
        "attempt_count",
        "completed_stages",
        "stage",
        "idempotency_key",
        "request_id",
        "input_snapshot_hash",
        "input_snapshot",
        "seed",
        "language",
    ):
        op.drop_column("generations", column)
    op.drop_constraint(
        "fk_generations_source_generation", "generations", type_="foreignkey"
    )
    op.drop_column("generations", "source_generation_id")
    op.alter_column("generations", "copy_json", new_column_name="marketing_text")
    op.add_column("generations", sa.Column("template", sa.String(50)))
    op.add_column("generations", sa.Column("tone", sa.String(50)))

    op.drop_column("products", "original_height")
    op.drop_column("products", "original_width")
    op.drop_column("products", "original_sha256")
    op.drop_column("products", "original_mime")
    op.add_column("products", sa.Column("cutout_image_url", sa.String()))
    op.alter_column(
        "products", "original_storage_key", new_column_name="original_image_url"
    )
    generation_stage.drop(op.get_bind(), checkfirst=True)
    campaign_language.drop(op.get_bind(), checkfirst=True)
