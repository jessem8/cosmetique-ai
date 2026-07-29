from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


DATABASE_URL = os.getenv("MIGRATION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="MIGRATION_TEST_DATABASE_URL is required for the destructive migration test",
)
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_generations_are_terminalized_on_upgrade():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL or "")
    command.upgrade(config, "0001")

    engine = create_engine(DATABASE_URL)
    user_id = uuid.uuid4()
    product_id = uuid.uuid4()
    generation_ids = [uuid.uuid4() for _ in range(4)]
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, hashed_password)
                VALUES (:id, :email, :password)
                """
            ),
            {
                "id": user_id,
                "email": "migration-test@example.com",
                "password": "legacy-hash",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO products (
                    id, user_id, name, category, original_image_url
                )
                VALUES (:id, :user_id, 'Sérum', 'soin', 'uploads/source.png')
                """
            ),
            {"id": product_id, "user_id": user_id},
        )
        for generation_id, legacy_status in zip(
            generation_ids,
            ("pending", "processing", "done", "error"),
            strict=True,
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO generations (
                        id, product_id, status, marketing_text
                    )
                    VALUES (
                        :id,
                        :product_id,
                        CAST(:status AS generationstatus),
                        'Legacy claim that must never be exposed'
                    )
                    """
                ),
                {
                    "id": generation_id,
                    "product_id": product_id,
                    "status": legacy_status,
                },
            )
        connection.execute(
            text(
                """
                INSERT INTO assets (
                    generation_id, format, url, width, height
                )
                VALUES (
                    :generation_id, 'instagram', 'legacy-output.jpg', 1080, 1080
                )
                """
            ),
            {"generation_id": generation_ids[2]},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT
                    status::text,
                    stage,
                    completed_stages,
                    attempt_count,
                    claimed_by,
                    heartbeat_at,
                    next_attempt_at,
                    remote_job_id,
                    runtime_id,
                    error_code,
                    error_message,
                    candidate_boxes,
                    artifact_manifest,
                    bundle_storage_key,
                    bundle_checksum,
                    copy_json,
                    completed_at
                FROM generations
                ORDER BY id
                """
            )
        ).mappings().all()

    assert len(rows) == 4
    for row in rows:
        assert row["status"] == "error"
        assert row["stage"] is None
        assert row["completed_stages"] == []
        assert row["attempt_count"] == 2
        assert row["claimed_by"] is None
        assert row["heartbeat_at"] is None
        assert row["next_attempt_at"] is None
        assert row["remote_job_id"] is None
        assert row["runtime_id"] is None
        assert row["error_code"] == "ARTIFACT_CONTRACT_FAILED"
        assert row["error_message"] == (
            "Les fichiers générés sont incomplets ou invalides."
        )
        assert row["candidate_boxes"] is None
        assert row["artifact_manifest"] is None
        assert row["bundle_storage_key"] is None
        assert row["bundle_checksum"] is None
        assert row["copy_json"] is None
        assert row["completed_at"] is not None
