from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.models import Generation
from app.services.worker import (
    browser_candidate_boxes,
    claim_statement,
    exhausted_lease_statement,
    expected_target_selection,
    is_transient_error,
    job_deadline_exceeded,
    remote_generation_input,
)
from ai_core.schemas import CandidateBox
from app.services.ai_client import (
    AIProtocolError,
    AIRuntimeLost,
    AIServiceUnavailable,
)
from app.services import worker as worker_module
from app.services.storage import GenerationInstall


def test_claim_query_uses_skip_locked_and_oldest_eligible_job_first():
    sql = str(
        claim_statement().compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY GENERATIONS.CREATED_AT ASC" in sql
    assert "GENERATIONS.NEXT_ATTEMPT_AT" in sql


def test_exhausted_stale_lease_is_terminalized_instead_of_sticking_processing():
    now = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
    sql = str(
        exhausted_lease_statement(now).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).upper()
    assert sql.startswith("UPDATE GENERATIONS SET")
    assert "GENERATIONS.STATUS = 'PROCESSING'" in sql
    assert "GENERATIONS.ATTEMPT_COUNT >= 2" in sql
    assert "GENERATIONS.HEARTBEAT_AT <" in sql
    assert "ERROR_CODE='GENERATION_STALE'" in sql


def test_only_transport_and_remote_availability_failures_are_transient():
    assert is_transient_error(AIServiceUnavailable("offline")) is True
    assert is_transient_error(AIProtocolError("bad payload")) is False
    assert is_transient_error(ValueError("bad request")) is False


def test_remote_job_deadline_is_bounded():
    now = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
    assert job_deadline_exceeded(now - timedelta(minutes=31), now, 30 * 60) is True
    assert job_deadline_exceeded(now - timedelta(minutes=29), now, 30 * 60) is False


def test_worker_maps_canonical_candidates_to_browser_boxes_without_loss():
    candidate = CandidateBox(
        id="candidate-1",
        score=0.91,
        type="box",
        x=0.1,
        y=0.2,
        width=0.3,
        height=0.4,
    )

    assert browser_candidate_boxes((candidate,)) == [
        {
            "id": "candidate-1",
            "score": 0.91,
            "type": "box",
            "x": 0.1,
            "y": 0.2,
            "width": 0.3,
            "height": 0.4,
        }
    ]


def test_worker_cleans_an_installed_bundle_when_database_finalize_fails(monkeypatch):
    generation_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    snapshot = SimpleNamespace(
        id=generation_id,
        request_id="request-a",
        input_snapshot_hash="a" * 64,
        seed=42,
        language="fr",
        input_snapshot={"generation": {"target_hint": None}},
        product=SimpleNamespace(original_width=8, original_height=10),
    )
    validated = SimpleNamespace(
        members={"manifest.json": b"manifest"},
        manifest={"artifacts": []},
        copy={},
        checksum="b" * 64,
    )
    cleanup_calls = []
    monkeypatch.setattr(worker_module, "validate_bundle", lambda *args, **kwargs: validated)
    monkeypatch.setattr(
        worker_module.storage,
        "install_generation",
        lambda *args, **kwargs: GenerationInstall(
            bundle_key=f"generations/{generation_id}/bundle.zip",
            artifact_keys={
                "manifest.json": f"generations/{generation_id}/manifest.json"
            },
            created=True,
        ),
    )
    monkeypatch.setattr(
        worker_module.storage,
        "delete_generation",
        lambda *args, **kwargs: cleanup_calls.append((args, kwargs)),
    )

    class SessionContext:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def begin(self):
            return self

    class FailingSession(SessionContext):
        def get(self, *args, **kwargs):
            raise RuntimeError("database finalize failed")

    class CleanupSession(SessionContext):
        def get(self, *args, **kwargs):
            return SimpleNamespace(
                status=worker_module.GenerationStatus.PROCESSING,
                claimed_by="worker-test",
                bundle_storage_key=None,
            )

    sessions = [FailingSession(), CleanupSession()]
    monkeypatch.setattr(worker_module, "SessionLocal", lambda: sessions.pop(0))

    with pytest.raises(RuntimeError, match="database finalize failed"):
        worker_module.GenerationWorker("worker-test")._install_success(
            snapshot, b"bundle", "runtime-a"
        )

    assert cleanup_calls == [
        ((str(generation_id),), {"expected_bundle_sha256": "b" * 64})
    ]


@pytest.mark.parametrize(
    ("target_hint", "expected_bound_target"),
    [
        (None, None),
        (
            {
                "type": "box",
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            {
                "type": "box",
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
        ),
    ],
)
def test_worker_binds_only_an_explicit_target_hint(
    monkeypatch, target_hint, expected_bound_target
):
    captured = {}
    snapshot = SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        request_id="request-a",
        input_snapshot_hash="a" * 64,
        seed=42,
        language="fr",
        input_snapshot={"generation": {"target_hint": target_hint}},
        product=SimpleNamespace(original_width=8, original_height=10),
    )

    def reject_after_capture(*args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after validation binding")

    monkeypatch.setattr(worker_module, "validate_bundle", reject_after_capture)

    with pytest.raises(RuntimeError, match="stop after validation binding"):
        worker_module.GenerationWorker("worker-test")._install_success(
            snapshot, b"bundle", "runtime-a"
        )

    if expected_bound_target is None:
        assert "expected_target_selection" not in captured
        assert captured["require_target_selection"] is True
    else:
        assert captured["expected_target_selection"] == expected_bound_target
        assert "require_target_selection" not in captured


def test_two_worker_lease_handoff_never_deletes_replayed_committed_artifacts(
    monkeypatch,
):
    generation_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    snapshot = SimpleNamespace(
        id=generation_id,
        request_id="request-a",
        input_snapshot_hash="a" * 64,
        seed=42,
        language="fr",
        input_snapshot={"generation": {"target_hint": None}},
        product=SimpleNamespace(original_width=8, original_height=10),
    )
    validated = SimpleNamespace(
        members={"manifest.json": b"manifest"},
        manifest={"artifacts": []},
        copy={},
        checksum="b" * 64,
    )
    monkeypatch.setattr(worker_module, "validate_bundle", lambda *args, **kwargs: validated)
    monkeypatch.setattr(
        worker_module.storage,
        "install_generation",
        lambda *args, **kwargs: GenerationInstall(
            bundle_key=f"generations/{generation_id}/bundle.zip",
            artifact_keys={
                "manifest.json": f"generations/{generation_id}/manifest.json"
            },
            created=False,
        ),
    )
    cleanup_calls = []
    monkeypatch.setattr(
        worker_module.storage,
        "delete_generation",
        lambda *args, **kwargs: cleanup_calls.append((args, kwargs)),
    )

    class HandoffSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def begin(self):
            return self

        def get(self, *args, **kwargs):
            return SimpleNamespace(
                status=worker_module.GenerationStatus.DONE,
                claimed_by=None,
                runtime_id="runtime-a",
            )

    monkeypatch.setattr(worker_module, "SessionLocal", HandoffSession)

    with pytest.raises(RuntimeError, match="non modifiable"):
        worker_module.GenerationWorker("worker-a")._install_success(
            snapshot, b"bundle", "runtime-a"
        )

    assert cleanup_calls == []


def test_failed_install_cleanup_stops_after_lease_moves_to_a_second_worker(
    monkeypatch,
):
    generation_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    cleanup_calls = []
    monkeypatch.setattr(
        worker_module.storage,
        "delete_generation",
        lambda *args, **kwargs: cleanup_calls.append((args, kwargs)),
    )

    class HandoffSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def begin(self):
            return self

        def get(self, *args, **kwargs):
            return SimpleNamespace(
                status=worker_module.GenerationStatus.PROCESSING,
                claimed_by="worker-b",
                bundle_storage_key=None,
            )

    monkeypatch.setattr(worker_module, "SessionLocal", HandoffSession)

    worker_module.GenerationWorker("worker-a")._cleanup_failed_install(
        generation_id, "b" * 64
    )

    assert cleanup_calls == []


def test_worker_checks_current_health_before_resuming_a_remote_job(monkeypatch):
    generation_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    generation = SimpleNamespace(
        id=generation_id,
        remote_job_id="job-abcdefghijklmnop",
        runtime_id="runtime-a",
        started_at=datetime.now(timezone.utc),
        product=SimpleNamespace(original_storage_key="products/original.png"),
    )
    calls = {"health": 0, "get_job": 0}

    class ChangedRuntimeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def health(self):
            calls["health"] += 1
            return SimpleNamespace(runtime_id="runtime-b")

        def get_job(self, *args, **kwargs):
            calls["get_job"] += 1
            raise AssertionError("old runtime must not be polled")

    worker = worker_module.GenerationWorker("worker-test")
    monkeypatch.setattr(worker, "_load", lambda value: generation)
    monkeypatch.setattr(worker, "_new_client", ChangedRuntimeClient)
    monkeypatch.setattr(worker_module.storage, "read_bytes", lambda key: b"image")
    failures = []
    monkeypatch.setattr(worker, "_fail_or_retry", lambda job_id, error: failures.append(error))

    worker.process(generation_id)

    assert calls == {"health": 1, "get_job": 0}
    assert len(failures) == 1
    assert isinstance(failures[0], AIRuntimeLost)


def test_remote_input_preserves_source_metadata_for_end_to_end_hash_verification():
    generation = {
        "language": "fr",
        "seed": 42,
        "audience": None,
        "benefits": [],
        "ingredients": [],
        "verified_claims": [],
        "cta": None,
        "creative_direction": None,
        "target_hint": None,
        "source_generation_id": None,
    }
    snapshot = {
        "product": {
            "name": "Sérum",
            "brand": "Maison",
            "category": "Soin",
            "original_mime": "image/png",
            "original_sha256": "a" * 64,
            "original_width": 640,
            "original_height": 800,
        },
        "generation": generation,
    }
    assert remote_generation_input(snapshot) == snapshot


def test_worker_binds_bundle_target_to_the_immutable_input_snapshot():
    target = {
        "type": "box",
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.4,
    }
    assert expected_target_selection({"generation": {"target_hint": target}}) == target
    assert expected_target_selection({"generation": {"target_hint": None}}) is None
