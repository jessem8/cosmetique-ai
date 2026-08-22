"""Campaign Studio V2 contracts, immutable lock revisions and cost ledger.

Revision ID: 0004
Revises: 0003

All V2 columns on the existing generation table are nullable (or have a
backwards-compatible operational default), so upgrading a populated V1
database never reinterprets or rewrites its historical jobs.  V2 lifecycle
state is stored separately from the V1 ``generationstatus`` enum.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # Existing V1 rows remain untouched.  No new V2 lifecycle enum is added to
    # PostgreSQL because a String column makes rolling upgrades and SQLite
    # contract tests safe while the V1 enum remains stable.
    op.add_column(
        "products",
        sa.Column("original_exif_orientation", sa.SmallInteger(), nullable=False, server_default="1"),
    )
    op.alter_column("products", "original_exif_orientation", server_default=None)

    generation_columns = (
        sa.Column("v2_contract_version", sa.String(20), nullable=True),
        sa.Column("lifecycle_status", sa.String(40), nullable=True),
        sa.Column("product_lock_revision_id", UUID, nullable=True),
        sa.Column("request_v2", JSONB, nullable=True),
        sa.Column("request_v2_hash", sa.String(64), nullable=True),
        sa.Column("provider_selection", JSONB, nullable=True),
        sa.Column("provider_execution_plan", JSONB, nullable=True),
        sa.Column("provider_execution_snapshot", JSONB, nullable=True),
        sa.Column("variant_count", sa.SmallInteger(), nullable=True),
        sa.Column("budget_authorized_micros", sa.BigInteger(), nullable=True),
        sa.Column("budget_reserved_micros", sa.BigInteger(), nullable=True),
        sa.Column("budget_charged_micros", sa.BigInteger(), nullable=True),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("unknown_remote_completion", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("retryable_error", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_provider_error_code", sa.String(80), nullable=True),
    )
    for column in generation_columns:
        op.add_column("generations", column)
    op.create_index(
        "ix_generations_lifecycle_status", "generations", ["lifecycle_status"]
    )

    op.create_table(
        "product_lock_revisions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("product_id", UUID, sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="processing"),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_storage_key", sa.String(500), nullable=False),
        sa.Column("source_mime", sa.String(50), nullable=False),
        sa.Column("source_width", sa.Integer(), nullable=False),
        sa.Column("source_height", sa.Integer(), nullable=False),
        sa.Column("source_exif_orientation", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("parent_revision_id", UUID, nullable=True),
        sa.Column("target_geometry", JSONB, nullable=True),
        sa.Column("mask_storage_key", sa.String(500), nullable=True),
        sa.Column("mask_sha256", sa.String(64), nullable=True),
        sa.Column("cutout_storage_key", sa.String(500), nullable=True),
        sa.Column("cutout_sha256", sa.String(64), nullable=True),
        sa.Column("prompts", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("scene_spec", JSONB, nullable=True),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model_provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"], ["product_lock_revisions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("product_id", "revision", name="uq_product_lock_product_revision"),
        sa.CheckConstraint("revision > 0", name="ck_product_lock_revision_positive"),
        sa.CheckConstraint("length(source_sha256) = 64", name="ck_product_lock_source_hash_length"),
    )
    op.create_index("ix_product_lock_revisions_id", "product_lock_revisions", ["id"])
    op.create_index("ix_product_lock_revisions_product_id", "product_lock_revisions", ["product_id"])
    op.create_index("ix_product_lock_revisions_owner_id", "product_lock_revisions", ["owner_id"])
    op.create_index("ix_product_lock_revisions_parent_revision_id", "product_lock_revisions", ["parent_revision_id"])
    op.create_index(
        "ix_product_lock_owner_status", "product_lock_revisions", ["owner_id", "status", "created_at"]
    )
    op.create_index(
        "ix_product_lock_product_status", "product_lock_revisions", ["product_id", "status", "revision"]
    )

    op.create_foreign_key(
        "fk_generations_product_lock_revision",
        "generations",
        "product_lock_revisions",
        ["product_lock_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_generations_product_lock_revision_id", "generations", ["product_lock_revision_id"]
    )

    op.create_table(
        "generation_attempts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("generation_id", UUID, sa.ForeignKey("generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False, server_default="accepted"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("request_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("usage_snapshot", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("generation_id", "attempt", name="uq_generation_attempt_number"),
    )
    op.create_index("ix_generation_attempts_generation_id", "generation_attempts", ["generation_id"])
    op.create_index(
        "ix_generation_attempt_provider_request", "generation_attempts", ["provider_request_id"]
    )

    op.create_table(
        "generation_variants",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("generation_id", UUID, sa.ForeignKey("generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_index", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("artifact_manifest", JSONB, nullable=True),
        sa.Column("qa_manifest", JSONB, nullable=True),
        sa.Column("provider_usage", JSONB, nullable=True),
        sa.Column("storage_key", sa.String(500), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("generation_id", "variant_index", name="uq_generation_variant_index"),
    )
    op.create_index("ix_generation_variants_id", "generation_variants", ["id"])
    op.create_index("ix_generation_variants_generation_id", "generation_variants", ["generation_id"])
    op.create_index("ix_generation_variant_status", "generation_variants", ["status", "created_at"])

    op.create_table(
        "provider_cost_ledger",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("generation_id", UUID, sa.ForeignKey("generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("ledger_key", sa.String(200), nullable=False),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="reserved"),
        sa.Column("authorized_micros", sa.BigInteger(), nullable=False),
        sa.Column("reserved_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("charged_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("usage_snapshot", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("ledger_key", name="uq_provider_cost_ledger_key"),
    )
    op.create_index("ix_provider_cost_ledger_id", "provider_cost_ledger", ["id"])
    op.create_index("ix_provider_cost_ledger_generation_id", "provider_cost_ledger", ["generation_id"])
    op.create_index("ix_provider_cost_generation", "provider_cost_ledger", ["generation_id", "attempt"])

    # Defaults are useful for newly inserted rows but are not left on V1
    # nullable contract columns, keeping old INSERT statements valid.
    for column in (
        "unknown_remote_completion",
        "cancellation_requested",
        "retryable_error",
    ):
        op.alter_column("generations", column, server_default=None)
    for table, column in (
        ("product_lock_revisions", "prompts"),
        ("product_lock_revisions", "metrics"),
        ("product_lock_revisions", "model_provenance"),
        ("generation_attempts", "outcome"),
        ("generation_attempts", "retryable"),
        ("generation_variants", "status"),
        ("provider_cost_ledger", "status"),
        ("provider_cost_ledger", "reserved_micros"),
        ("provider_cost_ledger", "charged_micros"),
    ):
        op.alter_column(table, column, server_default=None)


def downgrade() -> None:
    op.drop_index("ix_provider_cost_generation", table_name="provider_cost_ledger")
    op.drop_index("ix_provider_cost_ledger_generation_id", table_name="provider_cost_ledger")
    op.drop_index("ix_provider_cost_ledger_id", table_name="provider_cost_ledger")
    op.drop_table("provider_cost_ledger")

    op.drop_index("ix_generation_variant_status", table_name="generation_variants")
    op.drop_index("ix_generation_variants_generation_id", table_name="generation_variants")
    op.drop_index("ix_generation_variants_id", table_name="generation_variants")
    op.drop_table("generation_variants")

    op.drop_index("ix_generation_attempt_provider_request", table_name="generation_attempts")
    op.drop_index("ix_generation_attempts_generation_id", table_name="generation_attempts")
    op.drop_table("generation_attempts")

    op.drop_index("ix_generations_product_lock_revision_id", table_name="generations")
    op.drop_constraint("fk_generations_product_lock_revision", "generations", type_="foreignkey")

    op.drop_index("ix_product_lock_product_status", table_name="product_lock_revisions")
    op.drop_index("ix_product_lock_owner_status", table_name="product_lock_revisions")
    op.drop_index("ix_product_lock_revisions_owner_id", table_name="product_lock_revisions")
    op.drop_index("ix_product_lock_revisions_parent_revision_id", table_name="product_lock_revisions")
    op.drop_index("ix_product_lock_revisions_product_id", table_name="product_lock_revisions")
    op.drop_index("ix_product_lock_revisions_id", table_name="product_lock_revisions")
    op.drop_table("product_lock_revisions")

    op.drop_index("ix_generations_lifecycle_status", table_name="generations")
    for column in (
        "last_provider_error_code",
        "retryable_error",
        "cancellation_requested",
        "unknown_remote_completion",
        "provider_request_id",
        "budget_charged_micros",
        "budget_reserved_micros",
        "budget_authorized_micros",
        "variant_count",
        "provider_execution_snapshot",
        "provider_execution_plan",
        "provider_selection",
        "request_v2_hash",
        "request_v2",
        "product_lock_revision_id",
        "lifecycle_status",
        "v2_contract_version",
    ):
        op.drop_column("generations", column)
    op.drop_column("products", "original_exif_orientation")
