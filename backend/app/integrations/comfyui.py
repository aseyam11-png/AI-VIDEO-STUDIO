import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from app.core.config import settings


class ComfyUIError(RuntimeError):
    pass


class ComfyUICancelled(ComfyUIError):
    pass


SUPPORTED_WORKFLOWS = {
    "groundingdino_detect_v1": {"source_path", "prompt", "box_threshold", "text_threshold"},
    "sam2_track_v1": {"source_path", "prompt", "box_threshold", "text_threshold", "max_frames"},
    "wan22_ti2v_5b_v1": {
        "source_path", "prompt", "negative_prompt", "duration_seconds", "width", "height"
    },
    "realesrgan_upscale_v1": {"source_path", "scale"},
    "rife_interpolate_v1": {"source_path", "factor"},
}


def workflow_registry_path() -> Path:
    if settings.model_workflow_registry:
        return Path(settings.model_workflow_registry).expanduser().resolve()
    return Path(settings.media_root).expanduser().resolve() / "workflows" / "registry.json"


def load_workflow_registry() -> dict:
    path = workflow_registry_path()
    if not path.is_file():
        return {"version": 1, "workflows": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComfyUIError(f"Workflow registry is not valid JSON: {path.name}") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ComfyUIError("Workflow registry must be a version 1 JSON object.")
    workflows = value.get("workflows")
    if not isinstance(workflows, dict):
        raise ComfyUIError("Workflow registry must contain a workflows object.")
    return value


def build_workflow_prompt(workflow: dict, entry: dict, values: dict, workflow_id: str) -> dict:
    if workflow_id not in SUPPORTED_WORKFLOWS:
        raise ComfyUIError("Workflow ID is not allowlisted by the application.")
    if not isinstance(workflow, dict) or not workflow:
        raise ComfyUIError("ComfyUI API workflow must be a non-empty object.")
    bindings = entry.get("bindings")
    if not isinstance(bindings, dict) or not bindings:
        raise ComfyUIError(f"Workflow '{workflow_id}' has no configured input bindings.")

    allowed = SUPPORTED_WORKFLOWS[workflow_id]
    prompt = json.loads(json.dumps(workflow))
    for value_name, binding in bindings.items():
        if value_name not in allowed:
            raise ComfyUIError(f"Input '{value_name}' is not permitted for '{workflow_id}'.")
        if value_name not in values:
            raise ComfyUIError(f"Required workflow input '{value_name}' was not provided.")
        if not isinstance(binding, dict):
            raise ComfyUIError(f"Invalid binding for workflow input '{value_name}'.")
        node_id = str(binding.get("node_id", ""))
        input_name = str(binding.get("input_name", ""))
        if not node_id or not input_name or node_id not in prompt:
            raise ComfyUIError(f"Workflow binding '{value_name}' references an unknown node.")
        node = prompt[node_id]
        node_inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(node_inputs, dict) or input_name not in node_inputs:
            raise ComfyUIError(
                f"Workflow binding '{value_name}' references a missing node input."
            )
        node_inputs[input_name] = values[value_name]

    required = entry.get("required_inputs", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise ComfyUIError("Workflow required_inputs must be a list of strings.")
    missing = [item for item in required if item not in bindings or item not in values]
    if missing:
        raise ComfyUIError(f"Workflow is missing required bindings: {', '.join(missing)}")
    return prompt


def validate_workflow_definition(
    workflow_id: str,
    *,
    object_info: dict | None = None,
) -> bool:
    """Validate the workflow, node bindings, installed node types and listed model files."""
    defaults = {
        "source_path": "/tmp/studio-model-input",
        "prompt": "workflow validation",
        "negative_prompt": "",
        "box_threshold": 0.35,
        "text_threshold": 0.25,
        "max_frames": 8,
        "duration_seconds": 5,
        "width": 832,
        "height": 480,
        "scale": 2,
        "factor": 2,
    }
    client = ComfyUIClient()
    workflow, entry = client._load_workflow(workflow_id)
    build_workflow_prompt(workflow, entry, defaults, workflow_id)

    if object_info is not None:
        if not isinstance(object_info, dict):
            raise ComfyUIError("ComfyUI /object_info did not return a node catalog.")
        dynamic_inputs = {
            (str(binding.get("node_id")), str(binding.get("input_name")))
            for name, binding in entry.get("bindings", {}).items()
            if name in {"source_path", "prompt", "negative_prompt"}
            and isinstance(binding, dict)
        }
        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                raise ComfyUIError(f"Workflow node {node_id} is malformed.")
            class_type = node.get("class_type")
            node_info = object_info.get(class_type)
            if not isinstance(node_info, dict):
                raise ComfyUIError(f"Required ComfyUI node type '{class_type}' is not installed.")
            declared_inputs = node_info.get("input", {})
            if not isinstance(declared_inputs, dict):
                continue
            known_inputs = {}
            for section in ("required", "optional"):
                value = declared_inputs.get(section, {})
                if isinstance(value, dict):
                    known_inputs.update(value)
            node_inputs = node.get("inputs", {})
            if not isinstance(node_inputs, dict):
                raise ComfyUIError(f"Workflow node {node_id} has invalid inputs.")
            for input_name, input_value in node_inputs.items():
                if (str(node_id), str(input_name)) in dynamic_inputs:
                    continue
                definition = known_inputs.get(input_name)
                if not isinstance(definition, (list, tuple)) or not definition:
                    continue
                choices = definition[0]
                if (
                    isinstance(choices, list)
                    and choices
                    and all(isinstance(choice, str) for choice in choices)
                    and isinstance(input_value, str)
                    and input_value not in choices
                ):
                    raise ComfyUIError(
                        f"Workflow node {node_id} requires unavailable model/input file "
                        f"'{input_value}' for '{input_name}'."
                    )
    return True


class ComfyUIClient:
    def __init__(self) -> None:
        if not settings.comfyui_base_url:
            raise ComfyUIError("COMFYUI_BASE_URL is not configured.")
        self.base_url = settings.comfyui_base_url.rstrip("/")

    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if settings.comfyui_api_key:
            headers["Authorization"] = f"Bearer {settings.comfyui_api_key}"
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method="POST" if data is not None else "GET",
        )
        try:
            with urlopen(request, timeout=settings.comfyui_request_timeout_seconds) as response:
                content = response.read()
            return json.loads(content.decode("utf-8")) if content else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise ComfyUIError(f"ComfyUI returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ComfyUIError(f"ComfyUI is unavailable: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComfyUIError("ComfyUI returned an invalid JSON response.") from exc

    def health_check(self) -> bool:
        self._request("/system_stats")
        return True

    def _load_workflow(self, workflow_id: str) -> tuple[dict, dict]:
        if workflow_id not in SUPPORTED_WORKFLOWS:
            raise ComfyUIError("Workflow ID is not allowlisted by the application.")
        registry = load_workflow_registry()
        entry = registry["workflows"].get(workflow_id)
        if not isinstance(entry, dict):
            raise ComfyUIError(f"Workflow '{workflow_id}' is not configured.")
        filename = entry.get("file")
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.json", filename):
            raise ComfyUIError("Workflow file name is invalid.")
        root = workflow_registry_path().parent.resolve()
        workflow_path = (root / filename).resolve()
        if not workflow_path.is_relative_to(root) or not workflow_path.is_file():
            raise ComfyUIError(f"Workflow file for '{workflow_id}' was not found.")
        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ComfyUIError(f"Workflow file for '{workflow_id}' is invalid JSON.") from exc
        return workflow, entry

    def _download_output(self, item: dict, node_id: str, output_dir: Path) -> dict | None:
        filename = item.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            return None
        subfolder = item.get("subfolder", "")
        output_type = item.get("type", "output")
        if output_type != "output" or not isinstance(subfolder, str):
            return None
        if any(part in {".", ".."} for part in Path(subfolder).parts):
            return None
        extension = Path(filename).suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".gif", ".wav"}:
            return None

        query = urlencode({"filename": filename, "subfolder": subfolder, "type": output_type})
        headers = {}
        if settings.comfyui_api_key:
            headers["Authorization"] = f"Bearer {settings.comfyui_api_key}"
        request = Request(f"{self.base_url}/view?{query}", headers=headers)
        safe_node = re.sub(r"[^A-Za-z0-9_-]", "_", node_id)[:32]
        destination = (output_dir / f"{safe_node}_{filename}").resolve()
        if not destination.is_relative_to(output_dir.resolve()):
            raise ComfyUIError("ComfyUI output path escaped the project directory.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        size = 0
        try:
            with urlopen(request, timeout=settings.comfyui_request_timeout_seconds) as response:
                with temporary.open("wb") as stream:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > settings.max_model_output_bytes:
                            raise ComfyUIError("ComfyUI output exceeded the configured size limit.")
                        stream.write(chunk)
                    stream.flush()
            if size == 0:
                raise ComfyUIError("ComfyUI returned an empty output file.")
            temporary.replace(destination)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            temporary.unlink(missing_ok=True)
            raise ComfyUIError(f"Could not download a ComfyUI output: {exc}") from exc
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return {
            "node_id": node_id,
            "filename": filename,
            "saved_as": destination.name,
            "size_bytes": size,
            "media_type": extension.lstrip("."),
        }

    def run_workflow(
        self,
        workflow_id: str,
        values: dict,
        output_dir: Path,
        *,
        progress=None,
        cancel_check=None,
    ) -> dict:
        workflow, entry = self._load_workflow(workflow_id)
        prompt = build_workflow_prompt(workflow, entry, values, workflow_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        response = self._request("/prompt", {"prompt": prompt, "client_id": str(uuid4())})
        prompt_id = response.get("prompt_id")
        if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Fa-f0-9-]{32,36}", prompt_id):
            raise ComfyUIError("ComfyUI did not return a valid prompt_id.")
        if progress:
            progress(25, "comfyui_queued", f"Workflow {workflow_id} was accepted by ComfyUI.")

        deadline = time.monotonic() + settings.comfyui_job_timeout_seconds
        history_entry = None
        cancellation_requested = False
        while time.monotonic() < deadline:
            if cancel_check and cancel_check():
                # Keep the serialized model worker occupied until remote inference ends.
                # ComfyUI's interrupt endpoint is global and could stop another client.
                cancellation_requested = True
            history = self._request(f"/history/{prompt_id}")
            candidate = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(candidate, dict):
                status = candidate.get("status") or {}
                status_text = status.get("status_str")
                if status_text in {"error", "failed"}:
                    messages = status.get("messages", [])
                    raise ComfyUIError(f"ComfyUI workflow failed: {str(messages)[:1200]}")
                if isinstance(candidate.get("outputs"), dict):
                    history_entry = candidate
                    break
            time.sleep(settings.comfyui_poll_interval_seconds)
        if history_entry is None:
            raise ComfyUIError(f"ComfyUI workflow timed out. prompt_id={prompt_id}")
        if cancellation_requested or (cancel_check and cancel_check()):
            raise ComfyUICancelled(
                f"ComfyUI job {prompt_id} finished remotely after cancellation was requested."
            )

        output_metadata = []
        for node_id, node_output in history_entry.get("outputs", {}).items():
            if not isinstance(node_output, dict):
                continue
            for output_key in ("images", "gifs", "videos", "audio", "files"):
                items = node_output.get(output_key, [])
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        saved = self._download_output(item, str(node_id), output_dir)
                        if saved:
                            output_metadata.append(saved)

        if progress:
            progress(90, "comfyui_outputs", f"ComfyUI completed {workflow_id}.")
        return {
            "workflow_id": workflow_id,
            "prompt_id": prompt_id,
            "outputs": output_metadata,
            "output_node_ids": sorted(str(key) for key in history_entry.get("outputs", {})),
            "status": "completed",
        }
