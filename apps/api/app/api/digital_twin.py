from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dataset import Dataset, DatasetStatus
from app.models.digital_twin import DigitalTwin, TwinLayer, TwinLayerStatus
from app.models.project import Project
from app.schemas.dataset import DatasetRead
from app.schemas.digital_twin import (
    DigitalTwinRead,
    DigitalTwinRequest,
    DigitalTwinStateRead,
    TwinAcquisitionDateRangeRead,
    TwinExtentRead,
    TwinLayerRead,
    TwinLayerRequest,
    TwinLayerResult,
)
from app.services.digital_twin import (
    DIGITAL_TWIN_LIMITATIONS,
    AcquisitionDateInput,
    LayerExtentInput,
    compute_acquisition_date_range,
    compute_twin_extent,
    derive_layer_category,
    missing_recommended_layers,
)

router = APIRouter(tags=["digital-twin"])


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_twin_or_404(db: Session, twin_id: str) -> DigitalTwin:
    twin = db.get(DigitalTwin, twin_id)
    if twin is None:
        raise HTTPException(status_code=404, detail="Digital twin not found")
    return twin


def _get_registrable_dataset(db: Session, project_id: str, dataset_id: str) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Dataset not found in this project")
    if dataset.status != DatasetStatus.VALIDATED.value:
        raise HTTPException(
            status_code=400, detail=f"Dataset status is '{dataset.status}', not 'validated'."
        )
    return dataset


def _twin_layer_result(layer: TwinLayer, dataset: Dataset | None) -> TwinLayerResult:
    return TwinLayerResult(
        layer=TwinLayerRead.model_validate(layer),
        dataset=DatasetRead.model_validate(dataset) if dataset is not None else None,
    )


@router.post("/projects/{project_id}/digital-twin", response_model=DigitalTwinRead, status_code=201)
def create_digital_twin(
    project_id: str,
    payload: DigitalTwinRequest,
    db: Session = Depends(get_db),
) -> DigitalTwin:
    _get_project_or_404(db, project_id)
    existing = db.execute(select(DigitalTwin).where(DigitalTwin.project_id == project_id)).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A digital twin already exists for this project")

    twin = DigitalTwin(project_id=project_id, name=payload.name, description=payload.description, version=1)
    db.add(twin)
    db.commit()
    db.refresh(twin)
    return twin


@router.get("/projects/{project_id}/digital-twin", response_model=DigitalTwinRead)
def get_digital_twin_by_project(project_id: str, db: Session = Depends(get_db)) -> DigitalTwin:
    _get_project_or_404(db, project_id)
    twin = db.execute(select(DigitalTwin).where(DigitalTwin.project_id == project_id)).scalars().first()
    if twin is None:
        raise HTTPException(status_code=404, detail="No digital twin exists for this project")
    return twin


@router.get("/digital-twins/{twin_id}", response_model=DigitalTwinRead)
def get_digital_twin(twin_id: str, db: Session = Depends(get_db)) -> DigitalTwin:
    return _get_twin_or_404(db, twin_id)


@router.post(
    "/digital-twins/{twin_id}/layers",
    response_model=TwinLayerResult,
    status_code=201,
)
def register_twin_layer(
    twin_id: str,
    payload: TwinLayerRequest,
    db: Session = Depends(get_db),
) -> TwinLayerResult:
    twin = _get_twin_or_404(db, twin_id)
    dataset = _get_registrable_dataset(db, twin.project_id, payload.dataset_id)

    try:
        category = derive_layer_category(dataset.origin, dataset.dataset_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing_active = db.execute(
        select(TwinLayer).where(
            TwinLayer.twin_id == twin.id,
            TwinLayer.dataset_type == dataset.dataset_type,
            TwinLayer.status == TwinLayerStatus.ACTIVE.value,
        )
    ).scalars().first()

    twin.version += 1

    if existing_active is not None:
        existing_active.status = TwinLayerStatus.SUPERSEDED.value
        db.add(existing_active)

    layer = TwinLayer(
        twin_id=twin.id,
        dataset_id=dataset.id,
        dataset_type=dataset.dataset_type,
        category=category,
        status=TwinLayerStatus.ACTIVE.value,
        registered_at_version=twin.version,
        provenance={
            "registered_at_version": twin.version,
            "superseded_layer_id": existing_active.id if existing_active is not None else None,
            "dataset_type": dataset.dataset_type,
            "category": category,
        },
    )
    db.add(layer)
    db.add(twin)
    db.commit()
    db.refresh(layer)
    db.refresh(dataset)

    return _twin_layer_result(layer, dataset)


@router.get("/digital-twins/{twin_id}/layers", response_model=list[TwinLayerResult])
def list_twin_layers(
    twin_id: str,
    category: str | None = None,
    dataset_type: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
) -> list[TwinLayerResult]:
    _get_twin_or_404(db, twin_id)

    stmt = select(TwinLayer).where(TwinLayer.twin_id == twin_id)
    if category is not None:
        stmt = stmt.where(TwinLayer.category == category)
    if dataset_type is not None:
        stmt = stmt.where(TwinLayer.dataset_type == dataset_type)
    if status is None:
        stmt = stmt.where(TwinLayer.status == TwinLayerStatus.ACTIVE.value)
    elif status != "all":
        stmt = stmt.where(TwinLayer.status == status)
    stmt = stmt.order_by(TwinLayer.created_at.desc())

    layers = list(db.execute(stmt).scalars())
    results: list[TwinLayerResult] = []
    for layer in layers:
        dataset = db.get(Dataset, layer.dataset_id) if layer.dataset_id is not None else None
        results.append(_twin_layer_result(layer, dataset))
    return results


@router.get("/twin-layers/{layer_id}", response_model=TwinLayerResult)
def get_twin_layer(layer_id: str, db: Session = Depends(get_db)) -> TwinLayerResult:
    layer = db.get(TwinLayer, layer_id)
    if layer is None:
        raise HTTPException(status_code=404, detail="Twin layer not found")
    dataset = db.get(Dataset, layer.dataset_id) if layer.dataset_id is not None else None
    return _twin_layer_result(layer, dataset)


@router.post("/twin-layers/{layer_id}/retire", response_model=TwinLayerResult)
def retire_twin_layer(layer_id: str, db: Session = Depends(get_db)) -> TwinLayerResult:
    layer = db.get(TwinLayer, layer_id)
    if layer is None:
        raise HTTPException(status_code=404, detail="Twin layer not found")
    if layer.status != TwinLayerStatus.ACTIVE.value:
        raise HTTPException(
            status_code=400, detail=f"Twin layer status is '{layer.status}', not 'active'; cannot retire."
        )

    twin = _get_twin_or_404(db, layer.twin_id)
    twin.version += 1
    layer.status = TwinLayerStatus.RETIRED.value

    db.add(layer)
    db.add(twin)
    db.commit()
    db.refresh(layer)

    dataset = db.get(Dataset, layer.dataset_id) if layer.dataset_id is not None else None
    return _twin_layer_result(layer, dataset)


@router.get("/digital-twins/{twin_id}/state", response_model=DigitalTwinStateRead)
def get_digital_twin_state(twin_id: str, db: Session = Depends(get_db)) -> DigitalTwinStateRead:
    twin = _get_twin_or_404(db, twin_id)

    active_layers = list(
        db.execute(
            select(TwinLayer).where(
                TwinLayer.twin_id == twin_id, TwinLayer.status == TwinLayerStatus.ACTIVE.value
            )
        ).scalars()
    )

    extent_inputs: list[LayerExtentInput] = []
    acquisition_inputs: list[AcquisitionDateInput] = []
    layers_by_category: dict[str, list[TwinLayerResult]] = {}

    for layer in active_layers:
        dataset = db.get(Dataset, layer.dataset_id) if layer.dataset_id is not None else None
        bbox = None
        crs = None
        acquisition_date = None
        if dataset is not None:
            crs = dataset.crs
            acquisition_date = dataset.acquisition_date
            if None not in (
                dataset.bbox_min_x, dataset.bbox_min_y, dataset.bbox_max_x, dataset.bbox_max_y,
            ):
                bbox = (dataset.bbox_min_x, dataset.bbox_min_y, dataset.bbox_max_x, dataset.bbox_max_y)

        extent_inputs.append(LayerExtentInput(layer_id=layer.id, category=layer.category, crs=crs, bbox=bbox))
        acquisition_inputs.append(AcquisitionDateInput(layer_id=layer.id, acquisition_date=acquisition_date))

        layers_by_category.setdefault(layer.category, []).append(_twin_layer_result(layer, dataset))

    extent = compute_twin_extent(extent_inputs)
    acquisition_range = compute_acquisition_date_range(acquisition_inputs)
    categories_present = {layer.category for layer in active_layers}

    return DigitalTwinStateRead(
        twin=DigitalTwinRead.model_validate(twin),
        extent=TwinExtentRead(
            reference_crs=extent.reference_crs,
            bbox_min_x=extent.bbox[0] if extent.bbox else None,
            bbox_min_y=extent.bbox[1] if extent.bbox else None,
            bbox_max_x=extent.bbox[2] if extent.bbox else None,
            bbox_max_y=extent.bbox[3] if extent.bbox else None,
            crs_mismatch_layer_ids=extent.crs_mismatch_layer_ids,
            layers_without_extent=extent.layers_without_extent,
        ),
        acquisition_date_range=TwinAcquisitionDateRangeRead(
            earliest=acquisition_range.earliest,
            latest=acquisition_range.latest,
            layers_without_acquisition_date=acquisition_range.layers_without_acquisition_date,
        ),
        layers_by_category=layers_by_category,
        missing_recommended_layers=missing_recommended_layers(categories_present),
        limitations=list(DIGITAL_TWIN_LIMITATIONS),
    )
