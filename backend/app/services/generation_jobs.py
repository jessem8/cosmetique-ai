"""Pure generation job helpers shared by routes and the worker."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime
from typing import Any, TypeVar

from app.core.config import settings


class IdempotencyConflict(RuntimeError):
    pass


T = TypeVar("T")


def canonical_snapshot(snapshot: dict[str, Any]) -> bytes:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_snapshot(snapshot)).hexdigest()


def resolve_idempotency(
    existing: T | None, existing_hash: str | None, requested_hash: str
) -> T | None:
    if existing is None:
        return None
    if existing_hash != requested_hash:
        raise IdempotencyConflict("Cette clé a déjà servi pour une autre demande.")
    return existing


def _cursor_signature(payload: bytes) -> bytes:
    return hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).digest()[:16]


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(segment: str) -> bytes:
    if not segment or re.fullmatch(r"[A-Za-z0-9_-]+", segment) is None:
        raise ValueError("Segment de curseur invalide.")
    padding = "=" * (-len(segment) % 4)
    decoded = base64.urlsafe_b64decode(segment + padding)
    if _base64url_encode(decoded) != segment:
        raise ValueError("Segment de curseur non canonique.")
    return decoded


def encode_cursor(created_at: datetime, generation_id: uuid.UUID) -> str:
    payload = json.dumps(
        [created_at.isoformat(), str(generation_id)], separators=(",", ":")
    ).encode()
    return f"{_base64url_encode(payload)}.{_base64url_encode(_cursor_signature(payload))}"


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    if not cursor or len(cursor) > 512 or cursor.count(".") != 1:
        raise ValueError("Curseur invalide.")
    try:
        payload_segment, signature_segment = cursor.split(".")
        payload = _base64url_decode(payload_segment)
        signature = _base64url_decode(signature_segment)
        if len(signature) != 16 or not hmac.compare_digest(
            signature, _cursor_signature(payload)
        ):
            raise ValueError
        raw_datetime, raw_uuid = json.loads(payload)
        created_at = datetime.fromisoformat(raw_datetime)
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, uuid.UUID(raw_uuid)
    except (
        binascii.Error,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        raise ValueError("Curseur invalide.") from exc


SAFE_ERROR_MESSAGES: dict[str, str] = {
    "AI_SERVICE_UNAVAILABLE": "Le service de génération est temporairement indisponible.",
    "AI_RUNTIME_LOST": "La session GPU a été interrompue.",
    "REMOTE_AUTH_FAILED": "L'authentification du service de génération a échoué.",
    "REMOTE_PROTOCOL_ERROR": "La réponse du service de génération est invalide.",
    "TARGET_NOT_FOUND": "Aucun produit exploitable n'a été détecté.",
    "TARGET_AMBIGUOUS": "Plusieurs produits possibles ont été détectés.",
    "EXTRACTION_FAILED": "L'extraction du produit a échoué.",
    "MASK_QUALITY_FAILED": "Le détourage ne satisfait pas les critères de qualité.",
    "CLAIM_SAFETY_FAILED": "Le texte proposé ne respecte pas les faits vérifiés.",
    "INVALID_GENERATION_REQUEST": "La demande de génération est invalide.",
    "IDEMPOTENCY_CONFLICT": "Cette action a déjà été envoyée avec des données différentes.",
    "ARTIFACT_CONTRACT_FAILED": "Les fichiers générés sont incomplets ou invalides.",
    "ARTIFACT_CHECKSUM_MISMATCH": "L'intégrité des fichiers générés n'a pas pu être vérifiée.",
    "ARTIFACT_STORAGE_FAILED": "Les fichiers générés n'ont pas pu être stockés.",
    "GENERATION_STALE": "La génération a expiré avant sa finalisation.",
    "PROVIDER_RATE_LIMITED": "Le fournisseur est temporairement saturé.",
    "PROVIDER_BUDGET_EXCEEDED": "Le budget autorisé pour cette génération a été dépassé.",
    "PROVIDER_COMPLETION_UNKNOWN": "La fin de la génération doit être vérifiée.",
    "PROVIDER_PROTOCOL_ERROR": "La réponse du fournisseur est invalide.",
    "RETRY_EXHAUSTED": "La génération a échoué après les tentatives autorisées.",
    "INTERNAL_ERROR": "Une erreur interne a interrompu la génération.",
}


def safe_error_message(error_code: str) -> str:
    return SAFE_ERROR_MESSAGES.get(error_code, SAFE_ERROR_MESSAGES["INTERNAL_ERROR"])
