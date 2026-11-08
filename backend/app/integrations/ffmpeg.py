import json
import shutil
import subprocess
from pathlib import Path


class MediaToolError(RuntimeError):
    pass


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MediaToolError(f"Required binary not found: {name}")
    return path


def probe_media(path: str | Path) -> dict:
    """Probe media safely without invoking a shell."""
    media_path = Path(path).resolve()
    if not media_path.is_file():
        raise FileNotFoundError(media_path)
    ffprobe = require_binary("ffprobe")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(media_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise MediaToolError(result.stderr[-2000:])
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaToolError("ffprobe returned invalid JSON") from exc
