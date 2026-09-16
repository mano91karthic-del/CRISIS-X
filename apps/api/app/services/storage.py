import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings

settings = get_settings()


def dataset_storage_dir(project_id: str, dataset_id: str) -> Path:
    root = Path(settings.data_storage_root)
    path = root / project_id / dataset_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload(upload: UploadFile, project_id: str, dataset_id: str) -> tuple[Path, int, str]:
    """Streams an UploadFile to disk, returning (path, size_bytes, sha256_hex)."""

    directory = dataset_storage_dir(project_id, dataset_id)
    # Path(...).name strips any directory components from a hostile filename.
    filename = Path(upload.filename or f"upload-{uuid4().hex}").name
    destination = directory / filename

    sha256 = hashlib.sha256()
    size = 0
    with destination.open("wb") as out_file:
        while chunk := upload.file.read(1024 * 1024):
            sha256.update(chunk)
            size += len(chunk)
            out_file.write(chunk)

    return destination, size, sha256.hexdigest()
