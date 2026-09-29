import json
from pathlib import Path

import pytest

from app.services.clip import clip_vector_to_polygon

# A small AOI square, real-world-scale (~0.02deg on a side).
_AOI = {
    "type": "Polygon",
    "coordinates": [[[80.0, 13.0], [80.02, 13.0], [80.02, 13.02], [80.0, 13.02], [80.0, 13.0]]],
}


def _write_geojson(path: Path, geometry: dict, properties: dict | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": geometry, "properties": properties or {}}],
            }
        )
    )


def _write_multi_feature_geojson(path: Path, geometries: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": g, "properties": {}} for g in geometries],
            }
        )
    )


def test_clip_keeps_only_intersecting_features(tmp_path: Path) -> None:
    inside = {"type": "Point", "coordinates": [80.01, 13.01]}
    outside = {"type": "Point", "coordinates": [90.0, 20.0]}
    source_path = tmp_path / "source.geojson"
    aoi_path = tmp_path / "aoi.geojson"
    output_path = tmp_path / "clipped.geojson"
    _write_multi_feature_geojson(source_path, [inside, outside])
    _write_geojson(aoi_path, _AOI)

    result = clip_vector_to_polygon(source_path, aoi_path, output_path)

    assert result.metadata["source_feature_count"] == 2
    assert result.metadata["kept_feature_count"] == 1
    assert result.metadata["dropped_feature_count"] == 1
    assert output_path.exists()

    import geopandas as gpd

    kept = gpd.read_file(output_path)
    assert len(kept) == 1


def test_clip_raises_on_zero_overlap_instead_of_producing_empty_output(tmp_path: Path) -> None:
    outside = {"type": "Point", "coordinates": [90.0, 20.0]}
    source_path = tmp_path / "source.geojson"
    aoi_path = tmp_path / "aoi.geojson"
    output_path = tmp_path / "clipped.geojson"
    _write_geojson(source_path, outside)
    _write_geojson(aoi_path, _AOI)

    with pytest.raises(ValueError, match="do not spatially overlap"):
        clip_vector_to_polygon(source_path, aoi_path, output_path)
    assert not output_path.exists()


def test_clip_reprojects_aoi_to_source_crs_when_they_differ(tmp_path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import Point

    inside = Point(80.01, 13.01)
    source_path = tmp_path / "source.geojson"
    gpd.GeoDataFrame({"geometry": [inside]}, crs="EPSG:4326").to_file(source_path, driver="GeoJSON")

    # AOI expressed in UTM 44N (a real, different CRS) covering the same
    # real-world square as _AOI, expressed in meters instead of degrees.
    aoi_gdf = gpd.GeoDataFrame(geometry=[gpd.GeoSeries.from_wkt([f"POLYGON(({_wkt_ring()}))"])[0]], crs="EPSG:4326")
    aoi_path = tmp_path / "aoi.geojson"
    aoi_gdf.to_crs("EPSG:32644").to_file(aoi_path, driver="GeoJSON")

    output_path = tmp_path / "clipped.geojson"
    result = clip_vector_to_polygon(source_path, aoi_path, output_path)

    assert result.metadata["kept_feature_count"] == 1
    assert result.crs == "EPSG:4326"  # output stays in the SOURCE's own CRS


def _wkt_ring() -> str:
    coords = _AOI["coordinates"][0]
    return ", ".join(f"{x} {y}" for x, y in coords)
