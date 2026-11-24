import ast
import json
from pathlib import Path

NOTEBOOK_PATH = (
    Path(__file__).resolve().parents[1]
    / "notebooks"
    / "AI-VIDEO-STUDIO-COLAB-MODEL-LAB.ipynb"
)


def _load_notebook() -> dict:
    assert NOTEBOOK_PATH.is_file(), "Colab model-lab notebook is missing."
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    assert notebook.get("nbformat") == 4
    assert isinstance(notebook.get("cells"), list)
    return notebook


def test_colab_model_lab_notebook_is_valid_json_and_has_python_kernel():
    notebook = _load_notebook()

    assert notebook["metadata"]["kernelspec"]["name"] == "python3"
    assert notebook["metadata"]["accelerator"] == "GPU"
    assert any(
        cell.get("cell_type") == "markdown"
        and "Free-tier limitation" in "".join(cell.get("source", []))
        for cell in notebook["cells"]
    )


def test_every_colab_python_cell_parses():
    notebook = _load_notebook()
    code_cells = [
        cell for cell in notebook["cells"] if cell.get("cell_type") == "code"
    ]

    assert code_cells, "Notebook must contain executable Python cells."
    for index, cell in enumerate(code_cells):
        source = cell.get("source", [])
        source_text = "".join(source) if isinstance(source, list) else source
        ast.parse(source_text, filename=f"colab-notebook-cell-{index}")


def test_colab_ollama_api_is_loopback_only_and_not_a_worker():
    notebook = _load_notebook()
    all_source = "\n".join(
        "".join(cell.get("source", []))
        if isinstance(cell.get("source", []), list)
        else str(cell.get("source", ""))
        for cell in notebook["cells"]
    )
    lowered = all_source.lower()

    assert '127.0.0.1:11434' in all_source
    assert '0.0.0.0' not in all_source
    assert 'ngrok' not in lowered
    assert 'distributed worker' in lowered
    assert 'celery worker' in lowered
    assert 'systemctl' not in lowered
