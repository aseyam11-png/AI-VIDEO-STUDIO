import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from app.core.config import settings
from app.integrations.ffmpeg import MediaToolError, probe_media
from app.media.storage import project_dir


class BlenderError(RuntimeError):
    pass


def render_project_template(
    project_id: str,
    template_id: str,
    destination: Path,
    *,
    frame_start: int = 1,
    frame_end: int = 250,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Render an admin-provisioned .blend template; never accepts arbitrary script text."""
    if not template_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid Blender template identifier.")
    if frame_start < 1 or frame_end < frame_start or frame_end - frame_start > 9000:
        raise ValueError("Invalid Blender frame range.")
    if (
        width < 320
        or width > 3840
        or height < 240
        or height > 2160
        or fps not in {24, 25, 30, 50, 60}
    ):
        raise ValueError("Invalid Blender output settings.")

    executable = settings.blender_executable or shutil.which("blender")
    if not executable:
        raise BlenderError(
            "BLENDER_EXECUTABLE is not configured and Blender was not found on PATH."
        )

    root = project_dir(project_id).resolve()
    template = (root / "motion_templates" / f"{template_id}.blend").resolve()
    if not template.is_relative_to(root) or not template.is_file():
        raise BlenderError(
            "The requested Blender template is not installed in this project's "
            "motion_templates directory."
        )

    script = Path(__file__).with_name("blender_render_script.py").resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable), "--disable-autoexec", "-b", str(template), "--python", str(script), "--",
        "--output", str(destination.resolve()),
        "--frame-start", str(frame_start),
        "--frame-end", str(frame_end),
        "--width", str(width),
        "--height", str(height),
        "--fps", str(fps),
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    deadline = time.monotonic() + 7200
    output_text = ""
    while True:
        if cancel_check and cancel_check():
            process.terminate()
            try:
                output_text, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                output_text, _ = process.communicate()
            raise BlenderError(
                "Blender render was cancelled. " + (output_text or "")[-2000:]
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            output_text, _ = process.communicate()
            raise BlenderError(
                "Blender render timed out. " + (output_text or "")[-2000:]
            )
        try:
            output_text, _ = process.communicate(timeout=min(1.0, remaining))
            break
        except subprocess.TimeoutExpired as exc:
            if exc.output:
                output_text = exc.output if isinstance(exc.output, str) else exc.output.decode(
                    errors="replace"
                )
            continue

    if process.returncode != 0:
        raise BlenderError("Blender failed: " + (output_text or "")[-3000:])
    if not destination.is_file() or destination.stat().st_size == 0:
        raise BlenderError("Blender completed without creating the requested video.")
    try:
        metadata = probe_media(destination)
    except (MediaToolError, FileNotFoundError) as exc:
        raise BlenderError(f"Blender output could not be probed: {exc}") from exc
    if not any(stream.get("codec_type") == "video" for stream in metadata.get("streams", [])):
        raise BlenderError("Blender output does not contain a valid video stream.")
    return {
        "path": destination.name,
        "size_bytes": destination.stat().st_size,
        "metadata": metadata,
        "template_id": template_id,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "resolution": f"{width}x{height}",
        "fps": fps,
    }
