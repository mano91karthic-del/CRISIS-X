import hashlib
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetOrigin, DatasetStatus, DatasetType
from app.models.project import Project
from app.schemas.dataset import DatasetRead
from app.schemas.derive import ClipRequest, DeriveRequest
from app.schemas.preview import RasterPreviewBoundsRead
from app.services.dataset_gates import SourceNotEligible, ensure_metric_calibrated_if_terrain_x_import
from app.services.preview import (
    DEFAULT_COORDINATE_PRECISION,
    DEFAULT_GEOJSON_FEATURE_LIMIT,
    DEFAULT_MAX_PREVIEW_DIM,
    UnprojectableDatasetError,
    build_geojson_preview,
    build_heightmap,
    build_raster_preview,
    compute_heightmap_bounds,
    compute_raster_bounds_wgs84,
)
from app.services.storage import dataset_storage_dir, save_upload
from app.services.clip import clip_vector_to_polygon
from app.services.terrain import derive_terrain_product
from app.services.validation import VECTOR_EXTENSIONS, validate_dataset_file

DERIVATION_SOURCE_TYPES = {DatasetType.DEM.value, DatasetType.DSM.value}
_VECTOR_FORMATS = {ext.lstrip(".") for ext in VECTOR_EXTENSIONS}

router = APIRouter(tags=["datasets"])


@router.post("/projects/{project_id}/datasets", response_model=DatasetRead, status_code=201)
async def upload_dataset(
    project_id: str,
    dataset_type: DatasetType = Form(...),
    name: str | None = Form(None),
    # Optional, generic (not imagery-specific): when this data was
    # observed/recorded, if the user knows it. Never fabricated when
    # omitted -- see the resolution policy below and ADR 0006.
    acquisition_date: datetime | None = Form(None),
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
        origin=DatasetOrigin.UPLOADED.value,
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

    # Acquisition date resolution: the user's explicit claim takes
    # precedence over a tag extracted from the file (dates, unlike CRS,
    # aren't definitionally authoritative from the file alone -- a tag may
    # reflect file-creation time rather than true acquisition time). If
    # both are given and disagree, both are recorded rather than silently
    # resolved either way. If neither exists, acquisition_date stays NULL.
    extracted = result.extracted_acquisition_date
    if acquisition_date is not None:
        dataset.acquisition_date = acquisition_date
        if extracted is not None and extracted != acquisition_date:
            dataset.provenance = {
                **dataset.provenance,
                "acquisition_date_mismatch": True,
                "acquisition_date_supplied": acquisition_date.isoformat(),
                "acquisition_date_extracted_from_file": extracted.isoformat(),
            }
    else:
        dataset.acquisition_date = extracted

    db.commit()
    db.refresh(dataset)
    return dataset


@router.get("/projects/{project_id}/datasets", response_model=list[DatasetRead])
def list_datasets(
    project_id: str,
    # Plain str, not DatasetType: this also needs to match derived-product
    # types ("slope", "aspect"), which DatasetType intentionally excludes.
    dataset_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[Dataset]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    stmt = select(Dataset).where(Dataset.project_id == project_id)
    if dataset_type is not None:
        stmt = stmt.where(Dataset.dataset_type == dataset_type)
    stmt = stmt.order_by(Dataset.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


@router.post("/datasets/{dataset_id}/derive", response_model=DatasetRead, status_code=201)
def derive_dataset(dataset_id: str, payload: DeriveRequest, db: Session = Depends(get_db)) -> Dataset:
    source = db.get(Dataset, dataset_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source dataset not found")

    if source.dataset_type not in DERIVATION_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot derive '{payload.product}' from dataset_type '{source.dataset_type}'. "
                "Only DEM/DSM datasets can be used as a terrain-derivation source."
            ),
        )
    try:
        ensure_metric_calibrated_if_terrain_x_import(source)
    except SourceNotEligible as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if source.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Source dataset status is '{source.status}', not 'validated'. Cannot derive from it.",
        )

    source_path = Path(source.storage_path)
    if not source_path.exists():
        raise HTTPException(status_code=410, detail="Source dataset's stored file is missing")

    derived = Dataset(
        project_id=source.project_id,
        name=f"{source.name} — {payload.product}",
        dataset_type=payload.product,
        origin=DatasetOrigin.CRISISX_DERIVED.value,
        source_dataset_id=source.id,
        source_filename=f"{payload.product}.tif",
        storage_path="",
        file_size_bytes=0,
        checksum_sha256="",
        status=DatasetStatus.UPLOADED.value,
    )
    db.add(derived)
    db.flush()  # assigns derived.id without committing, so storage can key on it

    output_dir = dataset_storage_dir(source.project_id, derived.id)
    output_path = output_dir / f"{payload.product}.tif"

    try:
        result = derive_terrain_product(source_path, payload.product, output_path)
    except Exception as exc:
        derived.status = DatasetStatus.INVALID.value
        derived.validation_message = f"Derivation failed: {exc}"
        db.commit()
        db.refresh(derived)
        return derived

    checksum = hashlib.sha256(result.output_path.read_bytes()).hexdigest()

    derived.storage_path = str(result.output_path)
    derived.file_size_bytes = result.output_path.stat().st_size
    derived.checksum_sha256 = checksum
    derived.file_format = "geotiff"
    derived.crs = result.crs
    derived.bbox_min_x, derived.bbox_min_y, derived.bbox_max_x, derived.bbox_max_y = result.bbox
    derived.status = DatasetStatus.VALIDATED.value
    derived.metadata_json = result.metadata
    derived.provenance = {
        "source_dataset_id": source.id,
        "source_dataset_name": source.name,
        "source_filename": source.source_filename,
        **result.metadata,
    }

    db.commit()
    db.refresh(derived)
    return derived


@router.post("/datasets/{dataset_id}/clip", response_model=DatasetRead, status_code=201)
def clip_dataset(dataset_id: str, payload: ClipRequest, db: Session = Depends(get_db)) -> Dataset:
    """Clips a vector dataset to a registered STUDY_AREA dataset's polygon
    (see app/services/clip.py and ADR 0013) -- the mechanical enforcement
    of "every major layer must represent the same physical area." The
    clipped output is registered as its own new Dataset (same
    dataset_type as the source, so it slots into the twin/analysis
    pipeline exactly like the unclipped version would), never mutating
    the source in place.
    """
    source = db.get(Dataset, dataset_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source dataset not found")
    if source.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Source dataset status is '{source.status}', not 'validated'. Cannot clip it.",
        )
    if source.file_format not in _VECTOR_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot clip file_format '{source.file_format}' -- only vector datasets can be clipped.",
        )

    aoi = db.get(Dataset, payload.aoi_dataset_id)
    if aoi is None or aoi.project_id != source.project_id:
        raise HTTPException(status_code=404, detail="AOI dataset not found in this project")
    if aoi.dataset_type != DatasetType.STUDY_AREA.value:
        raise HTTPException(
            status_code=400,
            detail=f"AOI dataset_type is '{aoi.dataset_type}', not '{DatasetType.STUDY_AREA.value}'.",
        )
    if aoi.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(status_code=400, detail=f"AOI dataset status is '{aoi.status}', not 'validated'.")

    source_path = Path(source.storage_path)
    aoi_path = Path(aoi.storage_path)
    if not source_path.exists():
        raise HTTPException(status_code=410, detail="Source dataset's stored file is missing")
    if not aoi_path.exists():
        raise HTTPException(status_code=410, detail="AOI dataset's stored file is missing")

    clipped = Dataset(
        project_id=source.project_id,
        name=f"{source.name} (clipped to {aoi.name})",
        dataset_type=source.dataset_type,
        origin=DatasetOrigin.CRISISX_DERIVED.value,
        source_dataset_id=source.id,
        source_filename=f"{Path(source.source_filename or 'dataset').stem}_clipped.geojson",
        storage_path="",
        file_size_bytes=0,
        checksum_sha256="",
        status=DatasetStatus.UPLOADED.value,
    )
    db.add(clipped)
    db.flush()  # assigns clipped.id without committing, so storage can key on it

    output_dir = dataset_storage_dir(source.project_id, clipped.id)
    output_path = output_dir / clipped.source_filename

    try:
        result = clip_vector_to_polygon(source_path, aoi_path, output_path)
    except Exception as exc:
        clipped.status = DatasetStatus.INVALID.value
        clipped.validation_message = f"Clipping failed: {exc}"
        db.commit()
        db.refresh(clipped)
        return clipped

    checksum = hashlib.sha256(result.output_path.read_bytes()).hexdigest()

    clipped.storage_path = str(result.output_path)
    clipped.file_size_bytes = result.output_path.stat().st_size
    clipped.checksum_sha256 = checksum
    clipped.file_format = "geojson"
    clipped.crs = result.crs
    clipped.bbox_min_x, clipped.bbox_min_y, clipped.bbox_max_x, clipped.bbox_max_y = result.bbox
    clipped.status = DatasetStatus.VALIDATED.value
    clipped.metadata_json = result.metadata
    clipped.provenance = {
        "source_dataset_id": source.id,
        "source_dataset_name": source.name,
        "aoi_dataset_id": aoi.id,
        "aoi_dataset_name": aoi.name,
        "operation": "clip_to_study_area",
        **result.metadata,
    }

    db.commit()
    db.refresh(clipped)
    return clipped


@router.get("/datasets/{dataset_id}/derivatives", response_model=list[DatasetRead])
def list_derivatives(dataset_id: str, db: Session = Depends(get_db)) -> list[Dataset]:
    source = db.get(Dataset, dataset_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    stmt = (
        select(Dataset)
        .where(Dataset.source_dataset_id == dataset_id)
        .order_by(Dataset.created_at.desc())
    )
    return list(db.execute(stmt).scalars())


@router.get("/datasets/{dataset_id}/geojson")
def get_dataset_geojson(
    dataset_id: str,
    limit: int = Query(DEFAULT_GEOJSON_FEATURE_LIMIT, gt=0, le=200_000),
    precision: int = Query(DEFAULT_COORDINATE_PRECISION, ge=0, le=12),
    db: Session = Depends(get_db),
) -> dict:
    """Phase 11: WGS84 GeoJSON for the Command Dashboard's MapLibre
    `geojson` sources. Never returns the dataset's native CRS -- see
    app/services/preview.py::build_geojson_preview.
    """
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if (dataset.file_format or "").lower() not in _VECTOR_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Dataset file_format '{dataset.file_format}' is not a vector format; cannot generate a GeoJSON preview.",
        )
    path = Path(dataset.storage_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Stored file is missing")
    try:
        return build_geojson_preview(path, limit=limit, precision=precision)
    except UnprojectableDatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _get_raster_dataset_or_400(db: Session, dataset_id: str) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if (dataset.file_format or "").lower() != "geotiff":
        raise HTTPException(
            status_code=400,
            detail=f"Dataset file_format '{dataset.file_format}' is not a raster format; cannot generate a preview.",
        )
    path = Path(dataset.storage_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Stored file is missing")
    return dataset


@router.get("/datasets/{dataset_id}/preview.png")
def get_dataset_preview_png(
    dataset_id: str,
    max_dim: int = Query(DEFAULT_MAX_PREVIEW_DIM, gt=0, le=4096),
    db: Session = Depends(get_db),
) -> Response:
    """Phase 11: reprojected/downsampled/colorized PNG for the Command
    Dashboard's MapLibre `image` source. See
    app/services/preview.py::build_raster_preview.
    """
    dataset = _get_raster_dataset_or_400(db, dataset_id)
    class_legend = (dataset.metadata_json or {}).get("class_legend")
    try:
        result = build_raster_preview(
            Path(dataset.storage_path), dataset_type=dataset.dataset_type, class_legend=class_legend, max_dim=max_dim
        )
    except UnprojectableDatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=result.png_bytes, media_type="image/png")


@router.get("/datasets/{dataset_id}/preview-bounds", response_model=RasterPreviewBoundsRead)
def get_dataset_preview_bounds(dataset_id: str, db: Session = Depends(get_db)) -> RasterPreviewBoundsRead:
    """Phase 11: the WGS84 corner bounds `/preview.png` was rendered
    into -- paired with it to build a MapLibre `image` source's
    `coordinates` array.
    """
    dataset = _get_raster_dataset_or_400(db, dataset_id)
    try:
        (west, south, east, north), reprojection_applied = compute_raster_bounds_wgs84(Path(dataset.storage_path))
    except UnprojectableDatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RasterPreviewBoundsRead(
        west=west,
        south=south,
        east=east,
        north=north,
        native_crs=dataset.crs or "unknown",
        reprojection_applied=reprojection_applied,
        nodata_present=(dataset.metadata_json or {}).get("nodata") is not None,
    )


@router.get("/datasets/{dataset_id}/heightmap.png")
def get_dataset_heightmap_png(
    dataset_id: str,
    max_dim: int = Query(DEFAULT_MAX_PREVIEW_DIM, gt=0, le=4096),
    db: Session = Depends(get_db),
) -> Response:
    """Phase 11: native-CRS 8-bit grayscale heightmap for the Three.js
    terrain mesh. DEM/DSM only. See
    app/services/preview.py::build_heightmap for why this is NOT
    reprojected to WGS84.
    """
    dataset = _get_raster_dataset_or_400(db, dataset_id)
    if dataset.dataset_type not in DERIVATION_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"dataset_type '{dataset.dataset_type}' is not 'dem'/'dsm'; cannot generate a terrain heightmap.",
        )
    try:
        result = build_heightmap(Path(dataset.storage_path), max_dim=max_dim)
    except UnprojectableDatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=result.png_bytes, media_type="image/png")


@router.get("/datasets/{dataset_id}/heightmap-bounds", response_model=RasterPreviewBoundsRead)
def get_dataset_heightmap_bounds(
    dataset_id: str,
    max_dim: int = Query(DEFAULT_MAX_PREVIEW_DIM, gt=0, le=4096),
    db: Session = Depends(get_db),
) -> RasterPreviewBoundsRead:
    """Phase 11: native-CRS bounds + elevation range + pixel size (meters)
    paired with `/heightmap.png` to build a metrically-correct Three.js
    terrain mesh with no lon/lat math in the 3D scene. `max_dim` must
    match whatever value the paired `/heightmap.png` request uses (both
    default to `DEFAULT_MAX_PREVIEW_DIM`) -- the reported pixel size/
    bounds describe the SAME possibly-downsampled pixel grid the PNG
    actually encodes, not the raster's native resolution.
    """
    dataset = _get_raster_dataset_or_400(db, dataset_id)
    if dataset.dataset_type not in DERIVATION_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"dataset_type '{dataset.dataset_type}' is not 'dem'/'dsm'; cannot compute heightmap bounds.",
        )
    try:
        info = compute_heightmap_bounds(Path(dataset.storage_path), max_dim=max_dim)
    except UnprojectableDatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    west, south, east, north = info.bounds_native
    return RasterPreviewBoundsRead(
        west=west,
        south=south,
        east=east,
        north=north,
        native_crs=info.native_crs,
        reprojection_applied=False,
        nodata_present=info.nodata_present,
        elevation_min_m=info.elevation_min_m,
        elevation_max_m=info.elevation_max_m,
        pixel_size_x_m=info.pixel_size_x_m,
        pixel_size_y_m=info.pixel_size_y_m,
    )


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
