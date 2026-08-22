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


def test_install_cell_repairs_pillow_after_dependency_resolution() -> None:
    path = Path(__file__).parents[2] / "notebook" / "pipeline_ia_cosmetique.ipynb"
    notebook = nbformat.read(path, as_version=4)
    install_cell = next(cell for cell in notebook.cells if cell.get("id") == "install")
    source = "".join(install_cell["source"])

    assert 'Pillow==12.1.0' in source
    assert 'Pillow==12.3.0' not in source
    assert source.index('Pillow==12.1.0') > source.index('rembg onnxruntime-gpu')
