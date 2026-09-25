"""Phase 5: pairwise before/after EO change detection.

Two methods, both established and free of invented coefficients:

- Change Vector Analysis (CVA, Malila 1980): Euclidean magnitude of the
  per-band difference vector. No band semantics required -- the general-
  purpose default.
- Normalized difference: (A-B)/(A+B) computed per image then differenced.
  Requires the caller to explicitly identify which two bands to use --
  band roles (red/NIR/etc.) are never inferred from the file.

The one genuinely unsupervised technique offered is Otsu's automatic
thresholding (Otsu, 1979) -- classical histogram-based statistics, not a
trained/learned model, and explicitly not marketed as deep learning/AI. No
PyTorch, no training data, no labels: see docs/architecture/0006-phase-5-eo-change-detection.md
for the full reasoning.

Every result must be read alongside its `limitations`: observed image
change is never presented as confirmed real-world damage -- that
interpretation belongs to downstream modules with additional evidence.

The pure math (`compute_change_vector_magnitude`,
`compute_normalized_difference_index`, `compute_otsu_threshold`) is
separated from raster I/O (`run_change_detection`) so each piece is
testable independently, matching the pattern established in
app/services/terrain.py and app/services/hazard_flood.py.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.services.terrain import DERIVED_NODATA

CHANGE_DETECTION_LIMITATIONS = [
    "This is an observed image/spectral change detection result. It does not confirm, classify, "
    "or attribute the cause of change (e.g. flood damage, building destruction, deforestation, "
    "sensor/illumination differences). Interpretation and attribution require additional evidence "
    "and belong to downstream analysis, not this module.",
    "No cloud or shadow detection is performed; unmasked clouds/shadows in the input imagery may "
    "produce spurious change signals.",
    "No radiometric or atmospheric calibration is performed between acquisitions; differences may "
    "partly reflect sensor, illumination, or processing differences rather than true ground change.",
    "This module performs no trained/learned prediction; classification of change type or severity "
    "is out of scope.",
]

OTSU_LIMITATION = (
    "threshold_method='otsu' uses classical unsupervised statistical thresholding (Otsu, 1979) on "
    "the change-magnitude histogram -- not a trained or learned model, and not deep learning/AI. "
    "It assumes a roughly bimodal distribution; it may be unreliable for scenes with widespread "
    "uniform change or no clear separation."
)


def compute_change_vector_magnitude(
    before_bands: np.ndarray,
    after_bands: np.ndarray,
    nodata_before: float | None = None,
    nodata_after: float | None = None,
) -> np.ndarray:
    """Change Vector Analysis magnitude across the given bands.

    ``before_bands``/``after_bands`` are (n_bands, rows, cols) and must have
    the same shape. Returns a (rows, cols) magnitude array; NaN wherever
    either image is nodata at that cell for *any* of the selected bands.
    """
    if before_bands.shape != after_bands.shape:
        raise ValueError("before_bands and after_bands must have the same shape")
    if before_bands.ndim != 3:
        raise ValueError("before_bands/after_bands must be 3D (bands, rows, cols)")

    before = before_bands.astype("float64")
    after = after_bands.astype("float64")

    invalid = np.zeros(before.shape, dtype=bool)
    if nodata_before is not None:
        invalid |= np.isclose(before, nodata_before)
    if nodata_after is not None:
        invalid |= np.isclose(after, nodata_after)
    invalid_any_band = np.any(invalid, axis=0)

    diff = after - before
    magnitude = np.sqrt(np.sum(diff**2, axis=0))
    magnitude[invalid_any_band] = np.nan
    return magnitude


def compute_normalized_difference_index(
    band_a: np.ndarray, band_b: np.ndarray, nodata: float | None = None
) -> np.ndarray:
    """(A-B)/(A+B) for a single image's two bands. Division by zero is
    explicitly guarded -> NaN, never an unlabeled inf/NaN leaking through.
    """
    if band_a.shape != band_b.shape:
        raise ValueError("band_a and band_b must have the same shape")

    a = band_a.astype("float64")
    b = band_b.astype("float64")

    invalid = np.zeros(a.shape, dtype=bool)
    if nodata is not None:
        invalid |= np.isclose(a, nodata) | np.isclose(b, nodata)

    denom = a + b
    zero_denom = np.isclose(denom, 0.0)
    safe_denom = np.where(zero_denom, 1.0, denom)
    index = (a - b) / safe_denom
    index[zero_denom | invalid] = np.nan
    return index


def compute_normalized_difference_change(
    before_a: np.ndarray,
    before_b: np.ndarray,
    after_a: np.ndarray,
    after_b: np.ndarray,
    nodata_before: float | None = None,
    nodata_after: float | None = None,
) -> np.ndarray:
    index_before = compute_normalized_difference_index(before_a, before_b, nodata_before)
    index_after = compute_normalized_difference_index(after_a, after_b, nodata_after)
    return index_after - index_before


def compute_otsu_threshold(values: np.ndarray, bins: int = 256) -> float:
    """Otsu's method (1979): the threshold maximizing between-class variance
    of a histogram. Classical unsupervised statistics -- not a trained or
    learned model, no training data or labels used. ``values`` should
    already exclude NaN/nodata.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("no valid values to compute an automatic threshold from")

    vmin, vmax = float(finite.min()), float(finite.max())
    if vmin == vmax:
        raise ValueError(
            "cannot compute an automatic (Otsu) threshold: the data has no variation "
            "(all valid values are identical)"
        )

    hist, bin_edges = np.histogram(finite, bins=bins, range=(vmin, vmax))
    hist = hist.astype("float64")
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0

    total = hist.sum()
    sum_total = float(np.sum(hist * bin_centers))

    weight_background = np.cumsum(hist)
    sum_background = np.cumsum(hist * bin_centers)
    weight_foreground = total - weight_background

    with np.errstate(invalid="ignore", divide="ignore"):
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        between_class_variance = (
            weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        )

    between_class_variance = np.nan_to_num(
        between_class_variance, nan=-1.0, posinf=-1.0, neginf=-1.0
    )
    best_index = int(np.argmax(between_class_variance))
    return float(bin_centers[best_index])


def check_bounds_overlap(before_path: Path, after_path: Path) -> bool:
    """True if the two rasters' extents overlap at all, compared in the
    before image's CRS. A resampled-but-non-overlapping pair would
    otherwise silently produce an all-nodata result instead of a clear
    rejection -- this is meant to be checked before running an analysis.
    """
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(before_path) as before_src:
        before_bounds = before_src.bounds
        before_crs = before_src.crs

        with rasterio.open(after_path) as after_src:
            if after_src.crs == before_crs:
                after_bounds = tuple(after_src.bounds)
            else:
                after_bounds = transform_bounds(after_src.crs, before_crs, *after_src.bounds)

    left = max(before_bounds.left, after_bounds[0])
    bottom = max(before_bounds.bottom, after_bounds[1])
    right = min(before_bounds.right, after_bounds[2])
    top = min(before_bounds.top, after_bounds[3])

    return left < right and bottom < top


@dataclass
class _AlignedRasterPair:
    before: np.ndarray
    after: np.ndarray
    nodata_before: float | None
    nodata_after: float | None
    transform: Any
    crs: Any
    width: int
    height: int
    alignment_method: str
    resampling: str | None


def _load_aligned_pair(before_path: Path, after_path: Path) -> _AlignedRasterPair:
    """Reads both rasters, resampling `after` onto `before`'s exact grid if
    they don't already match (same crs/transform/dimensions). `before` is
    always the fixed reference grid -- a deliberate, consistent choice, not
    an arbitrary one made anew per call.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(before_path) as before_src:
        before_data = before_src.read()
        before_transform = before_src.transform
        before_crs = before_src.crs
        before_nodata = before_src.nodata
        before_width, before_height = before_src.width, before_src.height

        with rasterio.open(after_path) as after_src:
            after_nodata = after_src.nodata
            same_grid = (
                after_src.crs == before_crs
                and after_src.transform == before_transform
                and after_src.width == before_width
                and after_src.height == before_height
            )
            if same_grid:
                after_data = after_src.read().astype("float64")
                alignment_method = "identical_grid"
                resampling_name = None
            else:
                fill_value = after_nodata if after_nodata is not None else 0.0
                after_data = np.full(
                    (after_src.count, before_height, before_width), fill_value, dtype="float64"
                )
                for band_index in range(after_src.count):
                    reproject(
                        source=rasterio.band(after_src, band_index + 1),
                        destination=after_data[band_index],
                        src_transform=after_src.transform,
                        src_crs=after_src.crs,
                        dst_transform=before_transform,
                        dst_crs=before_crs,
                        resampling=Resampling.bilinear,
                        dst_nodata=after_nodata,
                    )
                alignment_method = "resampled_to_before_grid"
                resampling_name = "bilinear"

    return _AlignedRasterPair(
        before=before_data.astype("float64"),
        after=after_data.astype("float64"),
        nodata_before=before_nodata,
        nodata_after=after_nodata,
        transform=before_transform,
        crs=before_crs,
        width=before_width,
        height=before_height,
        alignment_method=alignment_method,
        resampling=resampling_name,
    )


@dataclass
class ChangeDetectionResult:
    magnitude_output_path: Path
    mask_output_path: Path
    crs: str
    bbox: tuple[float, float, float, float]
    magnitude_metadata: dict[str, Any]
    mask_metadata: dict[str, Any]


def run_change_detection(
    before_path: Path,
    after_path: Path,
    magnitude_output_path: Path,
    mask_output_path: Path,
    *,
    method: str,
    band_indices: list[int] | None = None,
    band_a_index: int | None = None,
    band_b_index: int | None = None,
    index_name: str | None = None,
    threshold_method: str,
    change_threshold: float | None = None,
) -> ChangeDetectionResult:
    if method not in ("change_vector_analysis", "normalized_difference"):
        raise ValueError(f"Unknown method '{method}'")
    if threshold_method not in ("manual", "otsu"):
        raise ValueError(f"Unknown threshold_method '{threshold_method}'")

    import rasterio
    from rasterio.transform import array_bounds

    pair = _load_aligned_pair(before_path, after_path)
    n_bands_before = pair.before.shape[0]
    n_bands_after = pair.after.shape[0]

    if method == "change_vector_analysis":
        if band_indices is None:
            common = min(n_bands_before, n_bands_after)
            resolved_band_indices = list(range(1, common + 1))
        else:
            resolved_band_indices = list(band_indices)
        for idx in resolved_band_indices:
            if idx < 1 or idx > n_bands_before or idx > n_bands_after:
                raise ValueError(
                    f"band index {idx} is out of range (before has {n_bands_before} bands, "
                    f"after has {n_bands_after} bands)"
                )
        zero_based = [i - 1 for i in resolved_band_indices]
        change = compute_change_vector_magnitude(
            pair.before[zero_based], pair.after[zero_based], pair.nodata_before, pair.nodata_after
        )
        method_metadata: dict[str, Any] = {"band_indices": resolved_band_indices}
    else:
        if band_a_index is None or band_b_index is None:
            raise ValueError("band_a_index and band_b_index are required for normalized_difference")
        for idx in (band_a_index, band_b_index):
            if idx < 1 or idx > n_bands_before or idx > n_bands_after:
                raise ValueError(
                    f"band index {idx} is out of range (before has {n_bands_before} bands, "
                    f"after has {n_bands_after} bands)"
                )
        change = compute_normalized_difference_change(
            pair.before[band_a_index - 1],
            pair.before[band_b_index - 1],
            pair.after[band_a_index - 1],
            pair.after[band_b_index - 1],
            pair.nodata_before,
            pair.nodata_after,
        )
        method_metadata = {
            "band_a_index": band_a_index,
            "band_b_index": band_b_index,
            "index_name": index_name,
        }

    valid = ~np.isnan(change)

    if threshold_method == "manual":
        if change_threshold is None:
            raise ValueError("change_threshold is required when threshold_method='manual'")
        resolved_threshold = change_threshold
    else:
        resolved_threshold = compute_otsu_threshold(np.abs(change[valid]))

    mask = np.full(change.shape, np.nan, dtype="float64")
    mask[valid] = (np.abs(change[valid]) >= resolved_threshold).astype("float64")

    magnitude_output = np.where(np.isnan(change), DERIVED_NODATA, change).astype("float32")
    mask_output = np.where(np.isnan(mask), DERIVED_NODATA, mask).astype("float32")

    for output_path, array in (
        (magnitude_output_path, magnitude_output),
        (mask_output_path, mask_output),
    ):
        with rasterio.open(
            output_path,
            "w",
            driver="GTiff",
            height=pair.height,
            width=pair.width,
            count=1,
            dtype="float32",
            crs=pair.crs,
            transform=pair.transform,
            nodata=DERIVED_NODATA,
        ) as dst:
            dst.write(array, 1)

    bbox = array_bounds(pair.height, pair.width, pair.transform)
    valid_count = int(np.count_nonzero(valid))
    changed_count = int(np.count_nonzero(mask[valid] == 1.0)) if valid_count else 0

    limitations = list(CHANGE_DETECTION_LIMITATIONS)
    if threshold_method == "otsu":
        limitations.append(OTSU_LIMITATION)

    shared_metadata = {
        "method": method,
        "alignment_method": pair.alignment_method,
        "resampling": pair.resampling,
        "threshold_method": threshold_method,
        "threshold_value": resolved_threshold,
        "valid_cell_count": valid_count,
        "changed_cell_count": changed_count,
        "limitations": limitations,
        **method_metadata,
    }

    return ChangeDetectionResult(
        magnitude_output_path=magnitude_output_path,
        mask_output_path=mask_output_path,
        crs=pair.crs.to_string(),
        bbox=tuple(bbox),
        magnitude_metadata={"product": "eo_change_magnitude", **shared_metadata},
        mask_metadata={"product": "eo_change_mask", **shared_metadata},
    )
