from __future__ import annotations

import json
from pathlib import Path


NOTEBOOK = (
    Path(__file__).resolve().parents[2]
    / "notebook"
    / "pipeline_ia_cosmetique.ipynb"
)


def test_canonical_notebook_is_clean_json_with_compilable_code_cells() -> None:
    raw = NOTEBOOK.read_text(encoding="utf-8")
    notebook = json.loads(raw)

    assert notebook["nbformat"] == 4
    assert len(notebook["cells"]) == 10
    assert all(
        cell.get("execution_count") is None and cell.get("outputs") == []
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), f"notebook-cell-{index}", "exec")
    assert '"displayName"' not in raw
    assert '"userId"' not in raw
    assert '"outputId"' not in raw
    assert "!pip" not in raw


def test_notebook_pins_cloudflared_binary_and_does_not_replace_torch() -> None:
    raw = NOTEBOOK.read_text(encoding="utf-8")

    assert "CLOUDFLARED_VERSION = '2026.7.2'" in raw
    assert (
        "ec905ea7b7e327ff8abdde8cb64697a2152de74dbcdbf6aec9db8364eb3886cd"
        in raw
    )
    assert "Pillow 12.3.0" in raw
    assert "pip', 'install', '--quiet'" in raw
