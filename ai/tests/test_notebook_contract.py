from __future__ import annotations

from pathlib import Path

import nbformat


NOTEBOOK = Path(__file__).parents[2] / "notebook" / "pipeline_ia_cosmetique.ipynb"


def _code_source() -> str:
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    return "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook.cells
        if cell.cell_type == "code"
    )


def test_canonical_notebook_is_valid_and_v2_only() -> None:
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    nbformat.validate(notebook)
    source = _code_source()
    lowered = source.casefold()
    for legacy in (
        "colab_cosmetic_poster_pipeline",
        "campaign_core",
        "generate_campaign_zip",
        "ollama",
        "run_public_server(",
        "pipeline_version = \"1.2.0\"",
    ):
        assert legacy not in lowered
    for required in (
        "colab_v2_service",
        "build_service",
        "run_public_v2_server",
        "codex/campaign-studio-upgrade-checkpoint",
        "COLAB_AI_TOKEN",
        "/v2/health",
    ):
        assert required in source


def test_notebook_has_the_deliberate_v2_execution_order() -> None:
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    ids = [cell.get("id") for cell in notebook.cells]
    assert ids == ["intro", "bootstrap", "source", "runtime", "smoke", "server"]
    assert "requirements-colab.lock" in _code_source()


def test_colab_lock_excludes_historical_v1_inference_dependencies() -> None:
    lock = (NOTEBOOK.parents[1] / "ai" / "requirements-colab.lock").read_text(encoding="utf-8")
    for legacy in ("rembg", "paddleocr", "paddlepaddle", "ollama"):
        assert legacy not in lock
    for required in ("diffusers==", "transformers==", "huggingface_hub==", "fastapi=="):
        assert required in lock
