import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import settings
from app.schemas.models import CreativePlan


class OllamaError(RuntimeError):
    pass


def _request_json(path: str, payload: dict | None = None) -> dict:
    if not settings.ollama_base_url:
        raise OllamaError("OLLAMA_BASE_URL is not configured.")
    url = f"{settings.ollama_base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=settings.ollama_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OllamaError(f"Ollama is unavailable: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OllamaError("Ollama returned an invalid JSON response.") from exc


def installed_model_names() -> set[str]:
    response = _request_json("/api/tags")
    models = response.get("models", [])
    return {
        str(item.get("name") or item.get("model"))
        for item in models
        if isinstance(item, dict) and (item.get("name") or item.get("model"))
    }


def is_model_installed() -> bool:
    return settings.ollama_model in installed_model_names()


def _parse_plan(content: str, target_duration_seconds: int) -> CreativePlan:
    value = content.strip()
    fence = chr(96) * 3
    if value.startswith(fence):
        value = value.removeprefix(fence + "json").removeprefix(fence).strip()
        if value.endswith(fence):
            value = value[:-3].strip()
    try:
        parsed = json.loads(value)
        plan = CreativePlan.model_validate(parsed)
    except (json.JSONDecodeError, ValueError) as exc:
        raise OllamaError(f"Qwen returned a plan that failed schema validation: {exc}") from exc
    if plan.target_duration_seconds != target_duration_seconds:
        raise OllamaError("Qwen returned a target duration different from the requested duration.")
    return plan


def generate_creative_plan(
    brief: str,
    *,
    target_duration_seconds: int = 60,
    language: str = "ar",
) -> CreativePlan:
    """Ask the configured local Qwen model for a validated plan; never pull weights."""
    if not brief.strip():
        raise ValueError("A creative brief is required.")
    if language not in {"ar", "en"}:
        raise ValueError("Language must be ar or en.")
    if not 1 <= target_duration_seconds <= 300:
        raise ValueError("Target duration must be between 1 and 300 seconds.")

    available = installed_model_names()
    if settings.ollama_model not in available:
        raise OllamaError(
            f"Required model '{settings.ollama_model}' is not installed in Ollama. "
            "Studio does not download weights automatically; install this model explicitly "
            "in the Ollama runtime before requesting a plan."
        )

    schema = CreativePlan.model_json_schema()
    system_prompt = (
        "You are the executive video director for an Arabic-first professional video studio. "
        "Convert the user's brief into a practical production plan. Return only JSON matching "
        "the supplied schema. Use the requested language. Scene indexes must start at 1 and be "
        "sequential. Scene durations must sum exactly to the requested target duration. "
        "Prefer achievable shots, clear camera movement, concrete visual direction, and concise "
        "on-screen text. Do not invent model capabilities, execute tools, write code, request "
        "credentials, or propose uploading media to external services. This plan is a draft and "
        "must be approved by the user before any generation or export."
    )
    user_prompt = (
        f"Language: {language}\n"
        f"Target duration in seconds: {target_duration_seconds}\n"
        f"User brief:\n{brief.strip()}"
    )
    response = _request_json(
        "/api/chat",
        {
            "model": settings.ollama_model,
            "stream": False,
            "format": schema,
            "keep_alive": "2m",
            "options": {"temperature": 0.3, "num_ctx": 8192},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
    )
    message = response.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise OllamaError("Qwen returned an empty response.")
    return _parse_plan(content, target_duration_seconds)
