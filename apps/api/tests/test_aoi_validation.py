"""Unit tests for app/services/aoi_validation.py -- the reusable
spatial-overlap check ADR 0013 item 30 requires ("tests that fail when
layers are spatially incompatible"). These use small synthetic fixtures
so they're fast and deterministic; a full real-data regression check
(e.g. "does the real Chennai buildings dataset overlap the real AOI")
belongs in a one-off audit script against the live project, not a unit
test that would otherwise depend on specific seeded project state.
"""

import json
from pathlib import Path

import pytest

from app.services.aoi_validation import bbox_intersects, feature_overlap_fraction


def _write_geojson(path: Path, geometries: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": g, "properties": {}} for g in geometries],
            }
        )
    )


_AOI = {
    "type": "Polygon",
    "coordinates": [[[80.0, 13.0], [80.02, 13.0], [80.02, 13.02], [80.0, 13.02], [80.0, 13.0]]],
}


class TestBboxIntersects:
    def test_overlapping_bboxes(self) -> None:
        assert bbox_intersects((0, 0, 10, 10), (5, 5, 15, 15)) is True

    def test_touching_bboxes_count_as_intersecting(self) -> None:
        assert bbox_intersects((0, 0, 10, 10), (10, 10, 20, 20)) is True

    def test_disjoint_bboxes(self) -> None:
        assert bbox_intersects((0, 0, 10, 10), (100, 100, 110, 110)) is False

    def test_a_large_bbox_fully_containing_a_small_one_intersects(self) -> None:
        # The "DEM covers the whole AOI and much more" case -- must not be
        # flagged as non-overlapping just because it's not a tight match.
        assert bbox_intersects((-100, -100, 100, 100), (0, 0, 10, 10)) is True

    def test_the_real_himalaya_vs_chennai_case_does_not_intersect(self) -> None:
        # output_SRTMGL1.tif's real bounds vs. the real Chennai AOI --
        # this is exactly the "wrong location entirely" failure this
        # function exists to catch.
        himalaya_bbox = (86.699, 27.500, 86.899, 27.700)
        chennai_aoi_bbox = (80.276, 13.084, 80.293, 13.104)
        assert bbox_intersects(himalaya_bbox, chennai_aoi_bbox) is False


class TestFeatureOverlapFraction:
    def test_all_features_inside_aoi(self, tmp_path: Path) -> None:
        dataset_path = tmp_path / "dataset.geojson"
        aoi_path = tmp_path / "aoi.geojson"
        _write_geojson(dataset_path, [{"type": "Point", "coordinates": [80.01, 13.01]}, {"type": "Point", "coordinates": [80.015, 13.015]}])
        _write_geojson(aoi_path, [_AOI])

        result = feature_overlap_fraction(dataset_path, aoi_path)
        assert result.total_feature_count == 2
        assert result.overlapping_feature_count == 2
        assert result.overlap_fraction == 1.0

    def test_no_features_inside_aoi(self, tmp_path: Path) -> None:
        dataset_path = tmp_path / "dataset.geojson"
        aoi_path = tmp_path / "aoi.geojson"
        _write_geojson(dataset_path, [{"type": "Point", "coordinates": [0.0, 0.0]}])
        _write_geojson(aoi_path, [_AOI])

        result = feature_overlap_fraction(dataset_path, aoi_path)
        assert result.overlapping_feature_count == 0
        assert result.overlap_fraction == 0.0

    def test_partial_overlap_matches_the_real_chennai_ratio_shape(self, tmp_path: Path) -> None:
        # Mirrors the real finding this ADR is built around: a dataset
        # where only a minority of features fall inside the AOI.
        dataset_path = tmp_path / "dataset.geojson"
        aoi_path = tmp_path / "aoi.geojson"
        inside = [{"type": "Point", "coordinates": [80.01, 13.01]} for _ in range(25)]
        outside = [{"type": "Point", "coordinates": [90.0, 20.0]} for _ in range(75)]
        _write_geojson(dataset_path, inside + outside)
        _write_geojson(aoi_path, [_AOI])

        result = feature_overlap_fraction(dataset_path, aoi_path)
        assert result.total_feature_count == 100
        assert result.overlapping_feature_count == 25
        assert result.overlap_fraction == pytest.approx(0.25)

    def test_empty_dataset_has_zero_fraction_not_a_crash(self, tmp_path: Path) -> None:
        dataset_path = tmp_path / "dataset.geojson"
        aoi_path = tmp_path / "aoi.geojson"
        _write_geojson(dataset_path, [])
        _write_geojson(aoi_path, [_AOI])

        result = feature_overlap_fraction(dataset_path, aoi_path)
        assert result.total_feature_count == 0
        assert result.overlap_fraction == 0.0

    def test_reprojects_aoi_to_dataset_crs_before_comparing(self, tmp_path: Path) -> None:
        import geopandas as gpd
        from shapely.geometry import Point

        dataset_path = tmp_path / "dataset.geojson"
        gpd.GeoDataFrame({"geometry": [Point(80.01, 13.01)]}, crs="EPSG:4326").to_file(dataset_path, driver="GeoJSON")

        aoi_gdf = gpd.GeoDataFrame(geometry=gpd.GeoSeries.from_wkt(
            [f"POLYGON(({', '.join(f'{x} {y}' for x, y in _AOI['coordinates'][0])}))"]
        ), crs="EPSG:4326")
        aoi_path = tmp_path / "aoi.geojson"
        aoi_gdf.to_crs("EPSG:32644").to_file(aoi_path, driver="GeoJSON")  # different CRS than the dataset

        result = feature_overlap_fraction(dataset_path, aoi_path)
        assert result.overlap_fraction == 1.0
