import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.core.config import settings
from app.db.models import AssetRecord, ProjectRecord
from app.db.session import session_scope


def _read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read legacy manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Legacy manifest must contain an object: {path}")
    return value


def _valid_id(value: str) -> str:
    return str(UUID(value))


def import_legacy_manifests() -> dict[str, int]:
    """Idempotently import Phase 0.5 JSON metadata without moving media files."""
    root = Path(settings.media_root).expanduser().resolve() / "projects"
    if not root.exists():
        return {"projects_imported": 0, "assets_imported": 0}

    project_count = 0
    asset_count = 0
    with session_scope() as db:
        for project_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            project_manifest_path = project_dir / "project.json"
            if not project_manifest_path.is_file():
                continue
            manifest = _read_manifest(project_manifest_path)
            project_id = _valid_id(str(manifest["id"]))
            if db.get(ProjectRecord, project_id) is None:
                created_at = datetime.fromisoformat(manifest["created_at"])
                db.add(
                    ProjectRecord(
                        id=project_id,
                        name=str(manifest["name"]),
                        target_duration_seconds=int(manifest["target_duration_seconds"]),
                        target_resolution=str(manifest.get("target_resolution", "1920x1080")),
                        target_fps=int(manifest.get("target_fps", 30)),
                        language=str(manifest.get("language", "ar")),
                        brief=str(manifest["brief"]),
                        status=str(manifest.get("status", "created")),
                        metadata_json={},
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
                project_count += 1

            assets_dir = project_dir / "assets"
            if not assets_dir.exists():
                continue
            for asset_dir in sorted(path for path in assets_dir.iterdir() if path.is_dir()):
                asset_manifest_path = asset_dir / "asset.json"
                if not asset_manifest_path.is_file():
                    continue
                asset_manifest = _read_manifest(asset_manifest_path)
                asset_id = _valid_id(str(asset_manifest["id"]))
                if db.get(AssetRecord, asset_id) is not None:
                    continue
                if _valid_id(str(asset_manifest["project_id"])) != project_id:
                    raise ValueError(f"Asset/project mismatch in legacy manifest: {asset_manifest_path}")
                created_at = datetime.fromisoformat(asset_manifest["created_at"])
                core_keys = {
                    "id", "project_id", "asset_type", "original_filename", "stored_filename",
                    "relative_path", "size_bytes", "sha256", "status", "created_at", "updated_at",
                }
                db.add(
                    AssetRecord(
                        id=asset_id,
                        project_id=project_id,
                        asset_type=str(asset_manifest["asset_type"]),
                        original_filename=str(asset_manifest["original_filename"]),
                        stored_filename=str(asset_manifest["stored_filename"]),
                        relative_path=str(asset_manifest["relative_path"]),
                        size_bytes=int(asset_manifest["size_bytes"]),
                        sha256=str(asset_manifest["sha256"]),
                        status=str(asset_manifest.get("status", "uploaded")),
                        metadata_json={
                            key: value for key, value in asset_manifest.items() if key not in core_keys
                        },
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
                asset_count += 1
    return {"projects_imported": project_count, "assets_imported": asset_count}
