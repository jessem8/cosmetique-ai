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
