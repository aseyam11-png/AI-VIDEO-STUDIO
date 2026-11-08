import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from app.core.config import settings
from app.integrations.ffmpeg import probe_media, require_binary


class AudioEnhancementError(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    timeout: int = 7200,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    deadline = time.monotonic() + timeout
    while True:
        if cancel_check and cancel_check():
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise AudioEnhancementError("Audio enhancement was cancelled.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            _, stderr = process.communicate()
            raise AudioEnhancementError(f"Audio command timed out: {stderr[-2000:]}")
        try:
            _, stderr = process.communicate(timeout=min(1.0, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    if process.returncode != 0:
        raise AudioEnhancementError(stderr[-3000:] or "Audio command failed.")


def enhance_video_audio(
    source: Path,
    destination: Path,
    *,
    post_filter: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Use an explicitly provisioned DeepFilterNet script and model directory."""
    script = Path(settings.deepfilter_script).expanduser().resolve() if settings.deepfilter_script else None
    model_dir = (
        Path(settings.deepfilter_model_dir).expanduser().resolve()
        if settings.deepfilter_model_dir
        else None
    )
    if script is None or not script.is_file():
        raise AudioEnhancementError("DEEPFILTER_SCRIPT must point to an installed DeepFilterNet enhance.py.")
    if model_dir is None or not model_dir.is_dir():
        raise AudioEnhancementError(
            "DEEPFILTER_MODEL_DIR must point to an existing local checkpoint directory. "
            "The Studio will not download audio model weights."
        )

    metadata = probe_media(source)
    if not any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", [])):
        raise AudioEnhancementError("The source video does not contain an audio stream.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deepfilter-", dir=destination.parent) as temporary:
        work_dir = Path(temporary)
        source_wav = work_dir / "source.wav"
        enhanced_dir = work_dir / "enhanced"
        enhanced_dir.mkdir()
        ffmpeg = require_binary("ffmpeg")
        _run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "48000",
                "-c:a", "pcm_s16le", str(source_wav),
            ],
            cancel_check=cancel_check,
        )
        command = [
            settings.deepfilter_python or sys.executable,
            str(script),
            "--model-base-dir", str(model_dir),
            "--output-dir", str(enhanced_dir),
        ]
        if post_filter:
            command.append("--pf")
        command.append(str(source_wav))
        _run(command, cancel_check=cancel_check)

        candidates = sorted(
            path for path in enhanced_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".wav", ".flac"}
        )
        if len(candidates) != 1:
            raise AudioEnhancementError(
                "DeepFilterNet did not produce exactly one enhanced audio file."
            )
        if cancel_check and cancel_check():
            raise AudioEnhancementError("Audio enhancement was cancelled.")

        _run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-i", str(candidates[0]),
                "-map", "0:v:0?", "-map", "1:a:0",
                "-map_metadata", "0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-shortest",
                "-movflags", "+faststart", str(destination),
            ],
            cancel_check=cancel_check,
        )

    return {
        "path": destination.name,
        "size_bytes": destination.stat().st_size,
        "metadata": probe_media(destination),
        "audio_model": "DeepFilterNet",
        "post_filter": post_filter,
    }
