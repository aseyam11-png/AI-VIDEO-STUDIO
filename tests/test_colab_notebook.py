import ast
import json
import re
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


def _all_notebook_source() -> str:
    notebook = _load_notebook()
    return "\n".join(
        "".join(cell.get("source", []))
        if isinstance(cell.get("source", []), list)
        else str(cell.get("source", ""))
        for cell in notebook["cells"]
    )


def test_colab_plan_validation_rejects_lossy_duration_coercion():
    source = _all_notebook_source()

    assert 'type(plan["target_duration_seconds"]) is int' in source
    assert 'type(scene["duration_seconds"]) is int' in source
    assert 'scene["duration_seconds"] == 5' in source
    assert 'sum(scene["duration_seconds"] for scene in plan["scenes"])' in source
    assert 'int(scene["duration_seconds"])' not in source


def test_colab_gpu_acceptance_is_scoped_to_the_selected_model():
    source = _all_notebook_source()

    assert 'line.split()[0] == MODEL_TAG' in source
    assert "processor_match = re.search(" in source
    assert 'r"\\b(?:\\d+(?:\\.\\d+)?%\\s*GPU|' in source
    assert "processor_match.group(0)" in source
    assert 'gpu_offload_observed = "GPU" in processor_line.upper()' in source
    assert '"selected_model_processor_line": processor_line' in source


 

def test_ollama_processor_pattern_accepts_gpu_and_split_offload_only():
    processor_pattern = re.compile(
        r"\\b(?:\\d+(?:\\.\\d+)?%\\s*GPU|"
        r"\\d+(?:\\.\\d+)?%/\\d+(?:\\.\\d+)?%\\s*CPU/GPU|"
        r"\\d+(?:\\.\\d+)?%\\s*CPU)\\b",
        re.IGNORECASE,
    )

    assert processor_pattern.search("qwen3.5:0.8b abc123 1.3 GB 100% GPU 4 minutes")
    assert processor_pattern.search("qwen3.5:0.8b abc123 1.3 GB 48%/52% CPU/GPU 4 minutes")
    cpu_only = processor_pattern.search("qwen3.5:0.8b abc123 1.3 GB 100% CPU 4 minutes")
    assert cpu_only and "GPU" not in cpu_only.group(0).upper()
