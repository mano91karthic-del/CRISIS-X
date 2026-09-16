from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetStatus, DatasetType
from app.models.project import Project
from app.schemas.dataset import DatasetRead
from app.services.storage import save_upload
from app.services.validation import validate_dataset_file

router = APIRouter(tags=["datasets"])


@router.post("/projects/{project_id}/datasets", response_model=DatasetRead, status_code=201)
async def upload_dataset(
    project_id: str,
    dataset_type: DatasetType = Form(...),
    name: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dataset:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    dataset = Dataset(
        project_id=project_id,
        name=name or (file.filename or "unnamed-dataset"),
        dataset_type=dataset_type.value,
        source_filename=file.filename or "unknown",
        storage_path="",
        file_size_bytes=0,
        checksum_sha256="",
        status=DatasetStatus.UPLOADED.value,
        provenance={"source_filename": file.filename},
    )
    db.add(dataset)
    db.flush()  # assigns dataset.id without committing, so storage can key on it

    destination, size, checksum = save_upload(file, project_id, dataset.id)
    result = validate_dataset_file(destination)

    dataset.storage_path = str(destination)
    dataset.file_size_bytes = size
    dataset.checksum_sha256 = checksum
    dataset.file_format = result.file_format
    dataset.crs = result.crs
    if result.bbox is not None:
        dataset.bbox_min_x, dataset.bbox_min_y, dataset.bbox_max_x, dataset.bbox_max_y = result.bbox
    dataset.status = result.status
    dataset.validation_message = result.message
    dataset.metadata_json = result.metadata

    db.commit()
    db.refresh(dataset)
    return dataset


@router.get("/projects/{project_id}/datasets", response_model=list[DatasetRead])
def list_datasets(
    project_id: str,
    dataset_type: DatasetType | None = None,
    db: Session = Depends(get_db),
) -> list[Dataset]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    stmt = select(Dataset).where(Dataset.project_id == project_id)
    if dataset_type is not None:
        stmt = stmt.where(Dataset.dataset_type == dataset_type.value)
    stmt = stmt.order_by(Dataset.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


@router.get("/datasets/{dataset_id}/download")
def download_dataset(dataset_id: str, db: Session = Depends(get_db)) -> FileResponse:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    path = Path(dataset.storage_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Stored file is missing")
    return FileResponse(path, filename=dataset.source_filename)


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: str, db: Session = Depends(get_db)) -> None:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    path = Path(dataset.storage_path)
    if path.exists():
        path.unlink()
        try:
            path.parent.rmdir()  # remove now-empty per-dataset directory, ignore if not empty
        except OSError:
            pass

    db.delete(dataset)
    db.commit()
