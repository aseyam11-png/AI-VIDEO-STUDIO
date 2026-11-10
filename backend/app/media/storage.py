import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select

from app.core.config import settings
from app.db.models import AssetRecord, ProjectRecord
from app.db.session import session_scope


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
    now = datetime.now(timezone.utc)
    manifest = {
        "id": project_id,
        "status": "created",
        "created_at": now.isoformat(),
        **project_data,
    }
    try:
        atomic_write_json(directory / "project.json", manifest)
        with session_scope() as db:
            db.add(
                ProjectRecord(
                    id=project_id,
                    name=manifest["name"],
                    target_duration_seconds=manifest["target_duration_seconds"],
                    target_resolution=manifest.get("target_resolution", "1920x1080"),
                    target_fps=manifest.get("target_fps", 30),
                    language=manifest.get("language", "ar"),
                    brief=manifest["brief"],
                    status="created",
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
            )
    except Exception:
        for child in directory.glob("*"):
            child.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return manifest


def get_project(project_id: str) -> dict:
    safe_id = _uuid(project_id)
    with session_scope() as db:
        record = db.get(ProjectRecord, safe_id)
        if record is not None:
            return record.to_dict()
    # Read-only compatibility for pre-Phase-1 manifests. Run the import command
    # before using durable jobs with projects created before the database existed.
    return read_json(project_dir(safe_id) / "project.json")


def get_asset(project_id: str, asset_id: str) -> dict:
    safe_project_id = _uuid(project_id)
    safe_asset_id = _uuid(asset_id)
    with session_scope() as db:
        record = db.scalar(
            select(AssetRecord).where(
                AssetRecord.project_id == safe_project_id,
                AssetRecord.id == safe_asset_id,
            )
        )
        if record is not None:
            return record.to_dict()
    return read_json(asset_dir(safe_project_id, safe_asset_id) / "asset.json")


def list_assets(project_id: str) -> list[dict]:
    safe_project_id = _uuid(project_id)
    with session_scope() as db:
        records = db.scalars(
            select(AssetRecord)
            .where(AssetRecord.project_id == safe_project_id)
            .order_by(AssetRecord.created_at, AssetRecord.id)
        ).all()
        if records:
            return [record.to_dict() for record in records]
    directory = project_dir(safe_project_id) / "assets"
    if not directory.exists():
        return []
    assets = []
    for child in sorted(directory.iterdir()):
        if child.is_dir():
            manifest_path = child / "asset.json"
            if manifest_path.is_file():
                assets.append(read_json(manifest_path))
    return assets


def update_asset_manifest(project_id: str, asset_id: str, updates: dict) -> dict:
    safe_project_id = _uuid(project_id)
    safe_asset_id = _uuid(asset_id)
    directory = asset_dir(safe_project_id, safe_asset_id)
    with session_scope() as db:
        record = db.scalar(
            select(AssetRecord).where(
                AssetRecord.project_id == safe_project_id,
                AssetRecord.id == safe_asset_id,
            )
        )
        manifest = record.to_dict() if record is not None else read_json(directory / "asset.json")
        manifest.update(updates)
        if record is not None:
            record.status = manifest.get("status", record.status)
            core_keys = {
                "id", "project_id", "asset_type", "original_filename", "stored_filename",
                "relative_path", "size_bytes", "sha256", "status", "created_at", "updated_at",
            }
            record.metadata_json = {
                key: value for key, value in manifest.items() if key not in core_keys
            }
    atomic_write_json(directory / "asset.json", manifest)
    return manifest


def ingest_file_stream(
    project_id: str,
    original_filename: str,
    source: BinaryIO,
    *,
    max_bytes: int,
) -> tuple[dict, Path]:
    """Persist media bytes outside the DB and durable asset metadata in the DB."""
    safe_project_id = _uuid(project_id)
    get_project(safe_project_id)
    suffix = Path(original_filename or "").suffix.lower()
    video_extensions = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
    image_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    audio_extensions = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
    allowed = video_extensions | image_extensions | audio_extensions
    if suffix not in allowed:
        raise ValueError(f"Unsupported media extension: {suffix or '(none)'}")

    identifier = str(uuid4())
    directory = asset_dir(safe_project_id, identifier)
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
        asset_type = (
            "image"
            if suffix in image_extensions
            else "audio"
            if suffix in audio_extensions
            else "video"
        )
        created_at = datetime.now(timezone.utc)
        manifest = {
            "id": identifier,
            "project_id": safe_project_id,
            "asset_type": asset_type,
            "original_filename": Path(original_filename).name[:240],
            "stored_filename": destination.name,
            "relative_path": destination.relative_to(project_dir(safe_project_id)).as_posix(),
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "created_at": created_at.isoformat(),
            "status": "uploaded",
        }
        atomic_write_json(directory / "asset.json", manifest)
        with session_scope() as db:
            if db.get(ProjectRecord, safe_project_id) is None:
                raise FileNotFoundError(
                    "Project metadata is not migrated. Run the legacy manifest import command."
                )
            db.add(
                AssetRecord(
                    id=identifier,
                    project_id=safe_project_id,
                    asset_type=asset_type,
                    original_filename=manifest["original_filename"],
                    stored_filename=destination.name,
                    relative_path=manifest["relative_path"],
                    size_bytes=size,
                    sha256=manifest["sha256"],
                    status="uploaded",
                    metadata_json={},
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        return manifest, destination
    except Exception:
        temporary.unlink(missing_ok=True)
        for child in directory.glob("*"):
            child.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass
        raise



def register_generated_video_asset(
    project_id: str,
    job_id: str,
    source_path: Path,
    *,
    max_bytes: int,
) -> dict:
    """Register a generated video under a stable job-derived asset ID.

    A retry of the same durable job updates the same asset instead of creating
    duplicate asset rows. Only files inside the project workspace are accepted.
    """
    safe_project_id = _uuid(project_id)
    safe_job_id = _uuid(job_id)
    root = project_dir(safe_project_id).resolve()
    source = source_path.expanduser().resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise StorageError("Generated video must be a file inside the project workspace.")
    suffix = source.suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        raise StorageError("Generated output is not a supported video format.")

    identifier = str(uuid5(NAMESPACE_URL, f"ai-video-studio:{safe_project_id}:{safe_job_id}"))
    directory = asset_dir(safe_project_id, identifier)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"source{suffix}.uploading"
    destination = directory / f"source{suffix}"
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
            while True:
                chunk = input_stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise StorageError("Generated output exceeds the configured maximum size.")
                digest.update(chunk)
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if size == 0:
            raise StorageError("Generated output is empty.")
        os.replace(temporary, destination)
        now = datetime.now(timezone.utc)
        manifest = {
            "id": identifier,
            "project_id": safe_project_id,
            "asset_type": "video",
            "original_filename": f"job-{safe_job_id[:8]}-{source.name}"[:240],
            "stored_filename": destination.name,
            "relative_path": destination.relative_to(root).as_posix(),
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "created_at": now.isoformat(),
            "status": "generated",
            "source_job_id": safe_job_id,
        }
        atomic_write_json(directory / "asset.json", manifest)
        with session_scope() as db:
            if db.get(ProjectRecord, safe_project_id) is None:
                raise FileNotFoundError("Project metadata is not available in PostgreSQL.")
            record = db.get(AssetRecord, identifier)
            if record is None:
                db.add(
                    AssetRecord(
                        id=identifier,
                        project_id=safe_project_id,
                        asset_type="video",
                        original_filename=manifest["original_filename"],
                        stored_filename=destination.name,
                        relative_path=manifest["relative_path"],
                        size_bytes=size,
                        sha256=manifest["sha256"],
                        status="generated",
                        metadata_json={"source_job_id": safe_job_id},
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                if record.project_id != safe_project_id:
                    raise StorageError("Generated asset ID is already assigned to another project.")
                record.original_filename = manifest["original_filename"]
                record.stored_filename = destination.name
                record.relative_path = manifest["relative_path"]
                record.size_bytes = size
                record.sha256 = manifest["sha256"]
                record.status = "generated"
                record.metadata_json = {"source_job_id": safe_job_id}
                record.updated_at = now
        return manifest
    except Exception:
        temporary.unlink(missing_ok=True)
        raise



def delete_asset_record(project_id: str, asset_id: str) -> None:
    safe_project_id = _uuid(project_id)
    safe_asset_id = _uuid(asset_id)
    with session_scope() as db:
        record = db.scalar(
            select(AssetRecord).where(
                AssetRecord.project_id == safe_project_id,
                AssetRecord.id == safe_asset_id,
            )
        )
        if record is not None:
            db.delete(record)
