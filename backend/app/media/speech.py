import gc
from collections.abc import Callable
from pathlib import Path

from app.media.pipeline import MediaJobCancelled
from app.media.storage import atomic_write_json


class SpeechDependencyError(RuntimeError):
    pass


def transcribe_local(
    source: Path,
    destination: Path,
    *,
    language: str = "ar",
    model_name: str = "small",
    device: str = "cpu",
    compute_type: str | None = None,
    cache_dir: Path,
    align: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Run WhisperX locally. Model downloads occur only after this explicit API call."""
    if language not in {"ar", "en"}:
        raise ValueError("Initial transcription supports language codes 'ar' and 'en'.")
    if model_name != "small":
        raise ValueError("This deployment is locked to the WhisperX small profile.")
    if device not in {"cpu", "cuda"}:
        raise ValueError("device must be 'cpu' or 'cuda'.")
    selected_compute_type = compute_type or ("int8" if device == "cpu" else "float16")
    try:
        import whisperx
    except ImportError as exc:
        raise SpeechDependencyError(
            "WhisperX is not installed. Install the optional speech worker dependencies first."
        ) from exc

    cache_dir.mkdir(parents=True, exist_ok=True)
    model = None
    align_model = None
    try:
        if cancel_check and cancel_check():
            raise MediaJobCancelled("Transcription was cancelled before model loading.")
        model = whisperx.load_model(
            model_name,
            device,
            compute_type=selected_compute_type,
            language=language,
            download_root=str(cache_dir),
        )
        audio = whisperx.load_audio(str(source))
        if cancel_check and cancel_check():
            raise MediaJobCancelled("Transcription was cancelled after model loading.")
        result = model.transcribe(audio, batch_size=4, language=language)
        if cancel_check and cancel_check():
            raise MediaJobCancelled("Transcription was cancelled after speech recognition.")
        alignment_status = "not_requested"
        alignment_error = None
        if align:
            try:
                align_model, metadata = whisperx.load_align_model(
                    language_code=language,
                    device=device,
                    model_dir=str(cache_dir),
                )
                result = whisperx.align(
                    result["segments"],
                    align_model,
                    metadata,
                    audio,
                    device,
                    return_char_alignments=False,
                )
                alignment_status = "completed"
            except MediaJobCancelled:
                raise
            except Exception as exc:  # Alignment support varies by language/model.
                alignment_status = "unavailable"
                alignment_error = str(exc)[:500]

        if cancel_check and cancel_check():
            raise MediaJobCancelled("Transcription was cancelled before saving its result.")
        payload = {
            "language": result.get("language", language),
            "model": model_name,
            "device": device,
            "compute_type": selected_compute_type,
            "alignment_status": alignment_status,
            "alignment_error": alignment_error,
            "segments": result.get("segments", []),
            "text": " ".join(
                str(segment.get("text", "")).strip()
                for segment in result.get("segments", [])
            ).strip(),
        }
        atomic_write_json(destination, payload)
        return payload
    finally:
        del model
        if align_model is not None:
            del align_model
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
