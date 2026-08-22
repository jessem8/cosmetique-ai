from __future__ import annotations

import json
from pathlib import Path

import nbformat


def test_canonical_notebook_is_valid_and_has_no_demo_copy() -> None:
    path = Path(__file__).parents[2] / "notebook" / "pipeline_ia_cosmetique.ipynb"
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)
    source = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook.cells
        if cell.cell_type == "code"
    ).casefold()
    for placeholder in ("maison exemple", "hydra glow serum", "sérum éclat"):
        assert placeholder not in source


def test_source_bootstrap_supports_an_explicit_uploaded_archive_without_force_reset() -> None:
    path = Path(__file__).parents[2] / "notebook" / "pipeline_ia_cosmetique.ipynb"
    notebook = nbformat.read(path, as_version=4)
    source_cell = next(cell for cell in notebook.cells if cell.get("id") == "source")
    source = "".join(source_cell["source"])

    assert "SOURCE_MODE" in source
    assert "uploaded_archive" in source
    assert "zipfile.ZipFile" in source
    assert "--force" not in source
    assert "shutil.rmtree(repo_dir" not in source
    assert "requirements-colab.lock" in source
    assert "CampaignStudioV2Runtime" in source


def test_install_cell_does_not_upgrade_the_inference_stack_unpinned() -> None:
    path = Path(__file__).parents[2] / "notebook" / "pipeline_ia_cosmetique.ipynb"
    notebook = nbformat.read(path, as_version=4)
    install_cell = next(cell for cell in notebook.cells if cell.get("id") == "install")
    source = "".join(install_cell["source"])

    assert "requirements-colab.lock" not in source
    assert "rembg onnxruntime-gpu paddleocr" not in source
    assert "huggingface_hub" in source


def test_install_cell_defers_inference_dependencies_to_the_pinned_repository_lock() -> None:
    path = Path(__file__).parents[2] / "notebook" / "pipeline_ia_cosmetique.ipynb"
    notebook = nbformat.read(path, as_version=4)
    install_cell = next(cell for cell in notebook.cells if cell.get("id") == "install")
    source = "".join(install_cell["source"])

    assert 'Pillow==' not in source
    assert 'rembg onnxruntime-gpu paddleocr' not in source
    assert 'huggingface_hub' in source
