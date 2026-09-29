"""Programmatic spatial-overlap checking between a dataset and a study
area (ADR 0013, "REQUIRED AUTOMATED DATA VALIDATION" -- item 30: tests
must fail when layers are spatially incompatible, not just when a
human eyeballs a bbox). Two distinct questions, deliberately kept
separate because they answer different real concerns:

- `bbox_intersects` -- does the dataset's extent touch the AOI at all?
  Appropriate for a large-extent raster/vector (a DEM tile, a regional
  hazard raster) that legitimately covers far more than the AOI --
  the only real failure mode there is "wrong location entirely" (e.g.
  a Himalayan DEM against a Chennai AOI), not "doesn't cover 100%".

- `feature_overlap_fraction` -- what fraction of a vector dataset's own
  FEATURES actually fall inside the AOI? Appropriate for a
  building/facility dataset whose own extent should mostly match the
  AOI -- this is what catches the "50,000 buildings, only 25% relevant"
  class of misalignment this ADR exists to fix.
"""

from dataclasses import dataclass
from pathlib import Path


def bbox_intersects(dataset_bbox: tuple[float, float, float, float], aoi_bbox: tuple[float, float, float, float]) -> bool:
    """Both bboxes must already be in the SAME CRS -- this is pure
    geometry, no reprojection. Callers with mismatched CRS should
    reproject first (see feature_overlap_fraction for a version that
    does this for them from real files).
    """
    d_minx, d_miny, d_maxx, d_maxy = dataset_bbox
    a_minx, a_miny, a_maxx, a_maxy = aoi_bbox
    return d_minx <= a_maxx and d_maxx >= a_minx and d_miny <= a_maxy and d_maxy >= a_miny


@dataclass
class OverlapResult:
    total_feature_count: int
    overlapping_feature_count: int
    overlap_fraction: float  # overlapping_feature_count / total_feature_count, 0.0 if total is 0


def feature_overlap_fraction(dataset_path: Path, aoi_path: Path) -> OverlapResult:
    """Reads both real vector files and computes what fraction of
    `dataset_path`'s features actually intersect `aoi_path`'s geometry --
    a real spatial computation (geopandas/shapely), never a bbox-corner
    approximation. Reprojects the AOI to the dataset's own CRS if they
    differ.
    """
    import geopandas as gpd

    dataset = gpd.read_file(dataset_path)
    aoi = gpd.read_file(aoi_path)

    if dataset.crs is None:
        raise ValueError(f"Dataset '{dataset_path.name}' has no defined CRS.")
    if aoi.crs is None:
        raise ValueError(f"AOI '{aoi_path.name}' has no defined CRS.")
    if aoi.crs != dataset.crs:
        aoi = aoi.to_crs(dataset.crs)

    total = int(len(dataset))
    if total == 0:
        return OverlapResult(total_feature_count=0, overlapping_feature_count=0, overlap_fraction=0.0)

    aoi_union = aoi.union_all() if hasattr(aoi, "union_all") else aoi.unary_union
    overlapping = int(dataset.intersects(aoi_union).sum())
    return OverlapResult(
        total_feature_count=total,
        overlapping_feature_count=overlapping,
        overlap_fraction=overlapping / total,
    )
