"""Phase 9: Digital Twin -- pure composition/aggregation functions over
already-existing Dataset metadata (crs, bbox_min/max_x/y, acquisition_date,
origin). No raster/vector file I/O, no new scientific computation: the
Digital Twin registers and organizes what Phases 2-8 already produced, it
never recomputes or duplicates any of it.

See docs/architecture/0010-phase-9-digital-twin.md for the full reasoning.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Verbatim reuse of the existing DatasetOrigin vocabulary -- no parallel
# taxonomy invented. Raises (via derive_layer_category) for anything not
# listed here rather than silently bucketing an unrecognized lineage fact.
CATEGORY_BY_ORIGIN = {
    "uploaded": "observation",
    "crisisx_derived": "terrain",
    "terrain_x_import": "terrain",
    "hazard_model": "hazard",
    "eo_analysis": "hazard",
    "exposure_analysis": "exposure",
    "risk_analysis": "risk",
    "route_analysis": "route",
}

# Highest priority first -- used to pick the Digital Twin's reference CRS:
# the terrain/DEM layer, if present, is the natural spatial anchor of "a
# place"; falls back down the list if no terrain layer is registered.
CATEGORY_PRIORITY = ["terrain", "hazard", "exposure", "risk", "route", "observation"]

# Soft, informational checklist only -- never a validation failure. A
# valid twin can legitimately lack any of these (e.g. an AOI with no road
# network has no usable route layer). "route" and "observation" are
# deliberately not on this list -- a route requires roads to exist in the
# AOI at all, and raw uploads are inputs, not something a twin is expected
# to have as an end state.
RECOMMENDED_CATEGORIES = ["terrain", "hazard", "exposure", "risk"]

DIGITAL_TWIN_LIMITATIONS = [
    "This Digital Twin is a composed registry of already-modeled/observed CRISIS-X datasets for this "
    "project; it is not a live sensor feed and does not reflect real-time conditions.",
    "It is not a prediction engine; hazard/risk/route layers reflect the modeled scenarios and "
    "assumptions recorded in their own provenance at the time they were computed, not a forecast of "
    "future conditions.",
    "This is not a perfect real-world replica; accuracy is bounded by the resolution, coverage, and "
    "assumptions of each registered layer's source data and model.",
    "Layers may have different acquisition dates, coordinate reference systems, and spatial extents; "
    "these are reported per layer, not silently reconciled.",
]


def derive_layer_category(origin: str, dataset_type: str | None = None) -> str:
    """Maps Dataset.origin and Dataset.dataset_type to a Digital Twin layer category."""
    if origin not in CATEGORY_BY_ORIGIN:
        raise ValueError(f"Unrecognized Dataset.origin '{origin}' for Digital Twin categorization.")

    if origin == "uploaded" and dataset_type in {"dem", "dsm"}:
        return "terrain"

    return CATEGORY_BY_ORIGIN[origin]


def missing_recommended_layers(categories_present: set[str]) -> list[str]:
    """Set difference against the fixed recommended-category checklist,
    in checklist order. Advisory only.
    """
    return [c for c in RECOMMENDED_CATEGORIES if c not in categories_present]


@dataclass
class LayerExtentInput:
    layer_id: str
    category: str
    crs: str | None
    bbox: tuple[float, float, float, float] | None  # (minx, miny, maxx, maxy)


@dataclass
class ExtentResult:
    reference_crs: str | None
    bbox: tuple[float, float, float, float] | None
    crs_mismatch_layer_ids: list[str]
    layers_without_extent: list[str]


def pick_reference_crs(layers: list[LayerExtentInput]) -> str | None:
    """Picks the reference CRS from the layer with the highest category
    priority that actually has a CRS. Never forces a particular
    projection (e.g. UTM) -- just anchors on whatever the most
    spatially-authoritative registered layer's native CRS already is.
    Returns None if no layer has a CRS at all (never fabricated).
    """
    by_category: dict[str, list[LayerExtentInput]] = {}
    for layer in layers:
        if layer.crs is not None:
            by_category.setdefault(layer.category, []).append(layer)

    for category in CATEGORY_PRIORITY:
        candidates = by_category.get(category)
        if candidates:
            return candidates[0].crs
    return None


def compute_twin_extent(layers: list[LayerExtentInput]) -> ExtentResult:
    """Unions layers' bounding boxes into one extent, reprojecting each
    into the reference CRS (via plain corner-point reprojection -- no
    raster/vector I/O, no forced UTM). Layers with no CRS or no bbox are
    excluded from the union and reported separately, never silently
    dropped without a trace. A layer whose native CRS differs from the
    reference is flagged, never silently reprojected without disclosure.
    """
    reference_crs = pick_reference_crs(layers)
    layers_without_extent = [l.layer_id for l in layers if l.crs is None or l.bbox is None]

    if reference_crs is None:
        return ExtentResult(
            reference_crs=None, bbox=None, crs_mismatch_layer_ids=[], layers_without_extent=layers_without_extent
        )

    from pyproj import Transformer

    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    crs_mismatch_layer_ids: list[str] = []
    any_contribution = False
    transformers: dict[str, Any] = {}

    for layer in layers:
        if layer.crs is None or layer.bbox is None:
            continue
        lx0, ly0, lx1, ly1 = layer.bbox
        if layer.crs != reference_crs:
            crs_mismatch_layer_ids.append(layer.layer_id)
            if layer.crs not in transformers:
                transformers[layer.crs] = Transformer.from_crs(layer.crs, reference_crs, always_xy=True)
            transformer = transformers[layer.crs]
            xs, ys = transformer.transform([lx0, lx1], [ly0, ly1])
            lx0, lx1 = min(xs), max(xs)
            ly0, ly1 = min(ys), max(ys)
        minx = min(minx, lx0)
        miny = min(miny, ly0)
        maxx = max(maxx, lx1)
        maxy = max(maxy, ly1)
        any_contribution = True

    if not any_contribution:
        return ExtentResult(
            reference_crs=reference_crs, bbox=None, crs_mismatch_layer_ids=[], layers_without_extent=layers_without_extent
        )

    return ExtentResult(
        reference_crs=reference_crs,
        bbox=(minx, miny, maxx, maxy),
        crs_mismatch_layer_ids=crs_mismatch_layer_ids,
        layers_without_extent=layers_without_extent,
    )


@dataclass
class AcquisitionDateInput:
    layer_id: str
    acquisition_date: datetime | None


@dataclass
class AcquisitionDateRange:
    earliest: datetime | None
    latest: datetime | None
    layers_without_acquisition_date: list[str]


def compute_acquisition_date_range(layers: list[AcquisitionDateInput]) -> AcquisitionDateRange:
    """Reports the earliest/latest acquisition date among layers that have
    one, and lists which layers don't -- never guesses a date for a layer
    that lacks one (Dataset.acquisition_date is itself "never fabricated";
    Phase 9 inherits that rule by construction, not by re-implementing it).
    """
    dated = [l for l in layers if l.acquisition_date is not None]
    undated = [l.layer_id for l in layers if l.acquisition_date is None]
    if not dated:
        return AcquisitionDateRange(earliest=None, latest=None, layers_without_acquisition_date=undated)
    dates = [l.acquisition_date for l in dated]
    return AcquisitionDateRange(earliest=min(dates), latest=max(dates), layers_without_acquisition_date=undated)
