"""One-shot diagnostic: re-download the last failed bundle and find the exact
contract check that rejects it. Read-only against DB + live tunnel."""
from __future__ import annotations

import sys

from sqlalchemy.orm import joinedload

from app.core.config import settings
from app.database import SessionLocal
from app.models import Generation
from app.services.ai_client import AIClient
from app.services.artifacts import (
    EXPECTED_DEPENDENCIES,
    EXPECTED_MODELS,
    RUNTIME_DEPENDENCY_NAMES,
    validate_bundle,
)
from app.services.generation_jobs import snapshot_hash
from app.services.worker import expected_target_selection
from ai_core.artifacts import PIPELINE_VERSION, validate_bundle as core_validate

GEN_PREFIX = sys.argv[1] if len(sys.argv) > 1 else "dbafd724"


def main() -> None:
    with SessionLocal() as db:
        rows = db.query(Generation).options(joinedload(Generation.product)).all()
    g = next((r for r in rows if str(r.id).startswith(GEN_PREFIX)), None)
    if g is None:
        print(f"NO_SUCH_GENERATION prefix={GEN_PREFIX}")
        return

    print(f"generation={g.id}")
    print(f"remote_job_id={g.remote_job_id}")
    print(f"runtime_id={g.runtime_id}")
    print(f"product_size={(g.product.original_width, g.product.original_height)}")
    print(f"expected_input_snapshot_hash={g.input_snapshot_hash}")
    print(f"recomputed_snapshot_hash={snapshot_hash(g.input_snapshot)}")
    print(f"seed={g.seed} language={g.language}")

    client = AIClient(base_url=settings.AI_SERVICE_URL, token=settings.AI_SERVICE_TOKEN)
    try:
        bundle = client.download_bundle(g.remote_job_id, expected_runtime_id=g.runtime_id)
    except Exception as exc:  # noqa: BLE001
        print(f"DOWNLOAD_FAILED type={type(exc).__name__} msg={exc}")
        return
    finally:
        client.close()
    print(f"bundle_bytes={len(bundle)}")

    try:
        manifest = core_validate(bundle)
    except Exception as exc:  # noqa: BLE001
        print(f"CORE_VALIDATE_FAILED type={type(exc).__name__} msg={exc}")
        return

    print("--- manifest field comparison ---")
    print(f"pipeline_version manifest={manifest.pipeline_version} expected={PIPELINE_VERSION}")
    print(f"runtime_id manifest={manifest.runtime_id} expected={g.runtime_id}")
    print(f"generation_id manifest={manifest.generation_id} expected={g.id}")
    print(f"request_id manifest={manifest.request_id} expected={g.request_id}")
    print(f"input_snapshot_hash manifest={manifest.input_snapshot_hash} expected={g.input_snapshot_hash}")
    print(f"seed manifest={manifest.seed} expected={g.seed}")
    print(f"language manifest={manifest.language.value} expected={g.language}")
    print(f"target_selection manifest={manifest.target_selection}")

    man_deps = {d.name.casefold(): d.version for d in manifest.dependencies}
    exp_dep_names = set(EXPECTED_DEPENDENCIES) | RUNTIME_DEPENDENCY_NAMES
    print("--- dependencies ---")
    print(f"names_match={set(man_deps) == exp_dep_names}")
    for name, version in sorted(EXPECTED_DEPENDENCIES.items()):
        got = man_deps.get(name)
        flag = "OK" if got == version else "MISMATCH"
        print(f"  [{flag}] {name}: manifest={got} expected={version}")
    extra = set(man_deps) - exp_dep_names
    missing = exp_dep_names - set(man_deps)
    if extra:
        print(f"  EXTRA_IN_MANIFEST={sorted(extra)}")
    if missing:
        print(f"  MISSING_FROM_MANIFEST={sorted(missing)}")

    print("--- models ---")
    print(f"models_match={manifest.models == EXPECTED_MODELS}")
    if manifest.models != EXPECTED_MODELS:
        for k in sorted(set(manifest.models) | set(EXPECTED_MODELS)):
            m = manifest.models.get(k)
            e = EXPECTED_MODELS.get(k)
            if m != e:
                print(f"  MISMATCH {k}: manifest={m} expected={e}")

    print("--- artifact dimensions ---")
    for rec in manifest.artifacts:
        print(f"  {rec.name.value}: {rec.width}x{rec.height} {rec.mime}")

    print("--- backend validate_bundle verdict ---")
    ts = expected_target_selection(g.input_snapshot)
    kwargs = {"expected_target_selection": ts} if ts is not None else {"require_target_selection": True}
    try:
        validate_bundle(
            bundle,
            expected_runtime_id=g.runtime_id,
            expected_generation_id=str(g.id),
            expected_request_id=g.request_id,
            expected_input_snapshot_hash=g.input_snapshot_hash,
            expected_seed=g.seed,
            expected_language=g.language,
            expected_source_size=(g.product.original_width, g.product.original_height),
            **kwargs,
        )
        print("VERDICT=ACCEPTED (bundle is valid!)")
    except Exception as exc:  # noqa: BLE001
        print(f"VERDICT=REJECTED type={type(exc).__name__} msg={exc}")


if __name__ == "__main__":
    main()
