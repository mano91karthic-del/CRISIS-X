"""Phase 4: slope-only landslide susceptibility screening.

Deliberately excludes aspect, elevation, curvature, and rainfall as scoring
factors:
- Aspect and elevation-based susceptibility relationships in the literature
  are regionally specific (solar exposure, geology, land-cover zonation)
  and not universal -- baking in a fixed directional/elevation assumption
  without regional calibration data would be an uncalibrated, effectively
  fabricated regional claim.
- Curvature is excluded because no curvature derivative exists anywhere in
  this system yet (Phase 2 deferred it; TERRAIN-X doesn't document it
  either) -- genuinely unavailable, not a design choice.
- Rainfall is accepted only as an optional descriptive label, recorded for
  reference, with zero effect on the computed class -- there is no
  legitimate rainfall-to-susceptibility coefficient available to apply.

See docs/architecture/0005-phase-4-hazard-engine.md for the full reasoning.
This is a susceptibility screening, not a prediction of an actual landslide.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_SLOPE_BREAKPOINTS_DEG: tuple[float, float, float, float] = (5.0, 15.0, 25.0, 35.0)

CLASS_LEGEND = {
    "1": "very_low",
    "2": "low",
    "3": "moderate",
    "4": "high",
    "5": "very_high",
}

NODATA_CLASS = -9999.0

LANDSLIDE_LIMITATIONS = [
    "Slope-only susceptibility screening; does not incorporate soil, geology, land cover, or seismic factors.",
    "Aspect, elevation, and curvature are not used as scoring factors (see ADR 0005 for the reasoning).",
    "Rainfall context, if provided, is recorded for reference only and does not currently influence the computed class.",
    "Slope breakpoints are a generic default unless explicitly overridden; not regionally calibrated.",
    "This is a susceptibility screening, not a prediction of whether or when a landslide will occur.",
]


def classify_slope(
    slope_degrees: np.ndarray,
    breakpoints: tuple[float, float, float, float],
    nodata: float | None = None,
) -> np.ndarray:
    """Classifies a slope raster (degrees) into 5 susceptibility classes
    (1=very_low .. 5=very_high) via 4 ascending breakpoints. Boundary
    convention: a value exactly at a breakpoint falls into the *higher*
    class (e.g. slope == breakpoints[1] falls in the class above it, not
    below) -- left-inclusive intervals. Returns NaN for nodata/NaN input
    cells.
    """
    if slope_degrees.ndim != 2:
        raise ValueError("slope_degrees must be a 2D array")
    if len(breakpoints) != 4:
        raise ValueError("breakpoints must contain exactly 4 values")
    if list(breakpoints) != sorted(breakpoints) or len(set(breakpoints)) != 4:
        raise ValueError("breakpoints must be strictly ascending")

    z = slope_degrees.astype("float64")
    invalid = np.isnan(z)
    if nodata is not None:
        invalid = invalid | np.isclose(z, nodata)

    classes = np.digitize(z, breakpoints) + 1  # 1..5
    result = classes.astype("float64")
    result[invalid] = np.nan
    return result


@dataclass
class LandslideScenarioResult:
    output_path: Path
    crs: str
    bbox: tuple[float, float, float, float]
    metadata: dict[str, Any]


def run_landslide_scenario(
    slope_path: Path,
    output_path: Path,
    *,
    slope_breakpoints_deg: tuple[float, float, float, float] = DEFAULT_SLOPE_BREAKPOINTS_DEG,
    rainfall_context: str | None = None,
) -> LandslideScenarioResult:
    import rasterio
    from rasterio.transform import array_bounds

    with rasterio.open(slope_path) as src:
        slope = src.read(1)
        nodata = src.nodata
        transform = src.transform
        crs = src.crs
        height, width = src.height, src.width

    classes = classify_slope(slope, tuple(slope_breakpoints_deg), nodata)
    output_array = np.where(np.isnan(classes), NODATA_CLASS, classes).astype("float32")

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=NODATA_CLASS,
    ) as dst:
        dst.write(output_array, 1)

    bbox = array_bounds(height, width, transform)

    class_cell_counts = {
        legend_label: int(np.count_nonzero(np.isclose(classes, float(class_value))))
        for class_value, legend_label in ((1, "very_low"), (2, "low"), (3, "moderate"), (4, "high"), (5, "very_high"))
    }

    return LandslideScenarioResult(
        output_path=output_path,
        crs=crs.to_string(),
        bbox=tuple(bbox),
        metadata={
            "hazard_type": "landslide",
            "method": "slope_threshold_classification",
            "slope_breakpoints_deg": list(slope_breakpoints_deg),
            "class_legend": CLASS_LEGEND,
            "class_cell_counts": class_cell_counts,
            "rainfall_context": rainfall_context,
            "crs": crs.to_string(),
            "limitations": LANDSLIDE_LIMITATIONS,
        },
    )
