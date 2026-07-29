from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.services import generation_jobs as generation_jobs_module
from app.services.generation_jobs import (
    IdempotencyConflict,
    canonical_snapshot,
    decode_cursor,
    encode_cursor,
    resolve_idempotency,
    snapshot_hash,
)


def test_snapshot_hash_is_stable_across_mapping_order():
    left = {"product": {"name": "Crème", "brand": None}, "seed": 42}
    right = {"seed": 42, "product": {"brand": None, "name": "Crème"}}

    assert canonical_snapshot(left) == canonical_snapshot(right)
    assert snapshot_hash(left) == snapshot_hash(right)
    assert len(snapshot_hash(left)) == 64


def test_idempotency_reuses_only_the_same_input_hash():
    existing = object()
    assert resolve_idempotency(existing, "same", "same") is existing
    with pytest.raises(IdempotencyConflict):
        resolve_idempotency(existing, "original", "different")


def test_cursor_round_trip_is_stable_for_equal_timestamps():
    created_at = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
    generation_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    encoded = encode_cursor(created_at, generation_id)

    assert decode_cursor(encoded) == (created_at, generation_id)


def test_cursor_round_trip_is_unambiguous_when_raw_signature_contains_a_dot(monkeypatch):
    monkeypatch.setattr(
        generation_jobs_module,
        "_cursor_signature",
        lambda payload: b"abc.defghijklmno",
    )
    created_at = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
    generation_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert decode_cursor(encode_cursor(created_at, generation_id)) == (
        created_at,
        generation_id,
    )


def test_many_cursor_signatures_round_trip_and_tampering_fails():
    baseline = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
    for index in range(512):
        created_at = baseline + timedelta(microseconds=index)
        generation_id = UUID(int=index + 1)
        cursor = encode_cursor(created_at, generation_id)
        assert cursor.count(".") == 1
        assert decode_cursor(cursor) == (created_at, generation_id)

        payload_segment, signature_segment = cursor.split(".")
        replacement = "A" if signature_segment[0] != "A" else "B"
        tampered = f"{payload_segment}.{replacement}{signature_segment[1:]}"
        with pytest.raises(ValueError):
            decode_cursor(tampered)


@pytest.mark.parametrize(
    "cursor", ["", "not-base64", "e30", "W10=", "AAAA.A", "AAAA..BBBB"]
)
def test_cursor_rejects_malformed_values(cursor):
    with pytest.raises(ValueError):
        decode_cursor(cursor)

