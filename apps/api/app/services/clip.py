"""Clips a vector dataset to a study-area polygon (ADR 0013: every major
CRISIS-X layer must represent the same physical area). Real spatial
intersection via geopandas/shapely -- never a bbox-corner approximation
-- so a feature is kept only if its actual geometry intersects the AOI,
not merely its bounding box. This is the one place in the pipeline that
enforces "do not mix unrelated geographic areas" mechanically rather
than by convention.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ClipResult:
    output_path: Path
    crs: str
    bbox: tuple[float, float, float, float]
    metadata: dict


def clip_vector_to_polygon(source_path: Path, aoi_path: Path, output_path: Path) -> ClipResult:
    """Reads `source_path` and `aoi_path` (both real vector files already
    on disk), reprojects the AOI to the source's own CRS if they differ,
    and writes only the features that actually intersect the AOI polygon
    to `output_path` as GeoJSON.

    Raises ValueError if either file has no CRS (never guessed, matching
    validation.py's own rule), or if the clip result is empty -- an empty
    result means the two datasets don't actually overlap, which is a real
    data problem to surface, not something to silently paper over with a
    fallback bbox.
    """
    import geopandas as gpd

    source = gpd.read_file(source_path)
    aoi = gpd.read_file(aoi_path)

    if source.crs is None:
        raise ValueError(f"Source dataset '{source_path.name}' has no defined CRS.")
    if aoi.crs is None:
        raise ValueError(f"AOI dataset '{aoi_path.name}' has no defined CRS.")

    if aoi.crs != source.crs:
        aoi = aoi.to_crs(source.crs)

    aoi_union = aoi.union_all() if hasattr(aoi, "union_all") else aoi.unary_union
    source_feature_count = int(len(source))
    kept = source[source.intersects(aoi_union)]
    kept_feature_count = int(len(kept))

    if kept_feature_count == 0:
        raise ValueError(
            f"Clipping '{source_path.name}' to the AOI kept 0 of {source_feature_count} features -- "
            "the two datasets do not spatially overlap. Refusing to produce an empty result."
        )

    kept.to_file(output_path, driver="GeoJSON")

    minx, miny, maxx, maxy = kept.total_bounds
    return ClipResult(
        output_path=output_path,
        crs=kept.crs.to_string(),
        bbox=(float(minx), float(miny), float(maxx), float(maxy)),
        metadata={
            "source_feature_count": source_feature_count,
            "kept_feature_count": kept_feature_count,
            "dropped_feature_count": source_feature_count - kept_feature_count,
            "geometry_types": sorted(kept.geom_type.dropna().unique().tolist()),
        },
    )
