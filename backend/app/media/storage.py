import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from app.core.config import settings


class StorageError(RuntimeError):
    pass


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Project and asset identifiers must be UUIDs.") from exc


def media_root() -> Path:
    root = Path(settings.media_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_dir(project_id: str) -> Path:
    safe_id = _uuid(project_id)
    path = (media_root() / "projects" / safe_id).resolve()
    if not path.is_relative_to(media_root()):
        raise ValueError("Invalid project path.")
    return path


def asset_dir(project_id: str, asset_id: str) -> Path:
    project_path = project_dir(project_id)
    safe_asset_id = _uuid(asset_id)
    path = (project_path / "assets" / safe_asset_id).resolve()
    if not path.is_relative_to(project_path):
        raise ValueError("Invalid asset path.")
    return path


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    with temporary.open("wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path.name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StorageError(f"Invalid JSON manifest: {path.name}") from exc


def create_project(project_data: dict) -> dict:
    project_id = str(uuid4())
    directory = project_dir(project_id)
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "id": project_id,
        "status": "created",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **project_data,
    }
    atomic_write_json(directory / "project.json", manifest)
    return manifest


def get_project(project_id: str) -> dict:
    return read_json(project_dir(project_id) / "project.json")


def get_asset(project_id: str, asset_id: str) -> dict:
    return read_json(asset_dir(project_id, asset_id) / "asset.json")


def list_assets(project_id: str) -> list[dict]:
    directory = project_dir(project_id) / "assets"
    if not directory.exists():
        return []
    assets = []
    for child in sorted(directory.iterdir()):
        if child.is_dir():
            manifest_path = child / "asset.json"
            if manifest_path.is_file():
                assets.append(read_json(manifest_path))
    return assets


def ingest_file_stream(
    project_id: str,
    original_filename: str,
    source: BinaryIO,
    *,
    max_bytes: int,
) -> tuple[dict, Path]:
    """Persist an already-open binary stream using an atomic temporary file."""
    get_project(project_id)
    suffix = Path(original_filename or "").suffix.lower()
    allowed = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp"}
    if suffix not in allowed:
        raise ValueError(f"Unsupported media extension: {suffix or '(none)'}")

    identifier = str(uuid4())
    directory = asset_dir(project_id, identifier)
    directory.mkdir(parents=True, exist_ok=False)
    temporary = directory / f"source{suffix}.uploading"
    destination = directory / f"source{suffix}"
    digest = hashlib.sha256()
    size = 0

    try:
        with temporary.open("wb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("Upload exceeds the configured maximum size.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size == 0:
            raise ValueError("Uploaded file is empty.")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass
        raise

    asset_type = "image" if suffix in {".jpg", ".jpeg", ".png", ".webp"} else "video"
    manifest = {
        "id": identifier,
        "project_id": _uuid(project_id),
        "asset_type": asset_type,
        "original_filename": Path(original_filename).name[:240],
        "stored_filename": destination.name,
        "relative_path": destination.relative_to(project_dir(project_id)).as_posix(),
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded",
    }
    atomic_write_json(directory / "asset.json", manifest)
    return manifest, destination
