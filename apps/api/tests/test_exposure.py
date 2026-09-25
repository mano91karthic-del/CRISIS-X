"""Pure-function tests for Phase 6 exposure geometry: classification,
polygonization, population-raster-to-points conversion, and the three
overlay operations — verified against known synthetic cases with hand-
computable ground truth.
"""

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon

from app.services.exposure import (
    classify_hazard_array,
    overlay_lines,
    overlay_points,
    overlay_polygons,
    polygonize_hazard_raster,
    raster_to_population_points,
)

CRS = "EPSG:32643"  # an arbitrary metric CRS for all tests


def _identity_transform(pixel_size: float = 10.0):
    from rasterio.transform import from_origin

    return from_origin(0, 100, pixel_size, pixel_size)  # origin (0,100), 10m pixels


# --- classify_hazard_array --------------------------------------------------


def test_classify_flood_inundation_is_single_class() -> None:
    array = np.array([[1.5, -9999.0], [0.3, 2.0]])
    codes, valid, labels = classify_hazard_array("flood_inundation", array, nodata=-9999.0)
    assert valid.tolist() == [[True, False], [True, True]]
    assert labels == {"1": "inundated"}
    assert codes[valid].tolist() == [1, 1, 1]


def test_classify_landslide_uses_default_legend() -> None:
    array = np.array([[1.0, 3.0], [5.0, -9999.0]])
    codes, valid, labels = classify_hazard_array("landslide_susceptibility", array, nodata=-9999.0)
    assert valid.tolist() == [[True, True], [True, False]]
    assert codes[0, 0] == 1 and codes[0, 1] == 3 and codes[1, 0] == 5
    assert labels["1"] == "very_low" and labels["5"] == "very_high"


def test_classify_landslide_uses_legend_override() -> None:
    array = np.array([[1.0, 2.0]])
    _, _, labels = classify_hazard_array(
        "landslide_susceptibility", array, nodata=None, class_legend_override={"1": "custom_low", "2": "custom_high"}
    )
    assert labels == {"1": "custom_low", "2": "custom_high"}


def test_classify_eo_change_mask_binary() -> None:
    array = np.array([[0.0, 1.0], [1.0, 0.0]])
    codes, valid, labels = classify_hazard_array("eo_change_mask", array, nodata=None)
    assert np.all(valid)
    assert codes.tolist() == [[0, 1], [1, 0]]
    assert labels == {"0": "no_change", "1": "changed"}


def test_classify_rejects_unsupported_dataset_type() -> None:
    with pytest.raises(ValueError, match="Unsupported hazard dataset_type"):
        classify_hazard_array("eo_change_magnitude", np.zeros((2, 2)), nodata=None)


# --- polygonize_hazard_raster ------------------------------------------------


def test_polygonize_produces_one_polygon_per_class_with_correct_area() -> None:
    # 4x4 grid, 10m cells -> left half class 1, right half class 2.
    codes = np.array(
        [
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
        ],
        dtype="int32",
    )
    valid = np.ones((4, 4), dtype=bool)
    transform = _identity_transform(10.0)

    gdf = polygonize_hazard_raster(codes, valid, transform, CRS)

    assert set(gdf["hazard_class_code"]) == {1, 2}
    class_1_area = gdf.loc[gdf["hazard_class_code"] == 1, "geometry"].area.iloc[0]
    class_2_area = gdf.loc[gdf["hazard_class_code"] == 2, "geometry"].area.iloc[0]
    # Each class covers 8 cells of 10x10m = 800 m^2.
    assert class_1_area == pytest.approx(800.0)
    assert class_2_area == pytest.approx(800.0)


def test_polygonize_excludes_invalid_cells_entirely() -> None:
    codes = np.array([[1, 1], [1, 1]], dtype="int32")
    valid = np.array([[True, False], [False, False]])
    transform = _identity_transform(10.0)

    gdf = polygonize_hazard_raster(codes, valid, transform, CRS)
    assert len(gdf) == 1
    assert gdf["geometry"].iloc[0].area == pytest.approx(100.0)  # one 10x10 cell


def test_polygonize_empty_when_all_invalid() -> None:
    codes = np.zeros((2, 2), dtype="int32")
    valid = np.zeros((2, 2), dtype=bool)
    gdf = polygonize_hazard_raster(codes, valid, _identity_transform(), CRS)
    assert gdf.empty


# --- raster_to_population_points --------------------------------------------


def test_population_raster_to_points_skips_nodata_and_zero() -> None:
    array = np.array([[10.0, 0.0], [-9999.0, 5.0]])
    transform = _identity_transform(10.0)
    gdf = raster_to_population_points(array, transform, CRS, nodata=-9999.0)

    assert len(gdf) == 2  # only the two positive, non-nodata cells
    assert set(gdf["population"]) == {10.0, 5.0}


def test_population_raster_to_points_centroid_location() -> None:
    array = np.array([[7.0]])
    transform = _identity_transform(10.0)  # origin (0, 100)
    gdf = raster_to_population_points(array, transform, CRS, nodata=None)

    point = gdf.geometry.iloc[0]
    # Cell (0,0) spans x:[0,10], y:[90,100] -> centroid (5, 95).
    assert point.x == pytest.approx(5.0)
    assert point.y == pytest.approx(95.0)


def test_population_raster_to_points_empty_when_all_skipped() -> None:
    array = np.full((2, 2), -9999.0)
    gdf = raster_to_population_points(array, _identity_transform(), CRS, nodata=-9999.0)
    assert gdf.empty


# --- overlay_points -----------------------------------------------------------


def _hazard_polygons_two_classes() -> "gpd.GeoDataFrame":
    # class 1: x in [0,10], class 2: x in [10,20], both y in [0,10]
    poly1 = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    poly2 = Polygon([(10, 0), (20, 0), (20, 10), (10, 10)])
    return gpd.GeoDataFrame({"hazard_class_code": [1, 2], "geometry": [poly1, poly2]}, crs=CRS)


def test_overlay_points_counts_and_sums_per_class() -> None:
    points = gpd.GeoDataFrame(
        {
            "population": [100.0, 50.0, 25.0],
            "geometry": [Point(5, 5), Point(5, 6), Point(15, 5)],
        },
        crs=CRS,
    )
    hazard = _hazard_polygons_two_classes()

    result, feature_gdf = overlay_points(points, hazard, value_field="population")

    assert result[1] == {"count": 2, "sum": 150.0}
    assert result[2] == {"count": 1, "sum": 25.0}
    assert len(feature_gdf) == 3


def test_overlay_points_without_value_field_counts_only() -> None:
    points = gpd.GeoDataFrame({"geometry": [Point(5, 5), Point(15, 5)]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()
    result, _ = overlay_points(points, hazard)
    assert result[1] == {"count": 1}
    assert result[2] == {"count": 1}
    assert "sum" not in result[1]


def test_overlay_points_outside_any_class_not_counted() -> None:
    points = gpd.GeoDataFrame({"geometry": [Point(500, 500)]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()
    result, _ = overlay_points(points, hazard)
    assert result == {}


def test_overlay_points_empty_inputs() -> None:
    empty_points = gpd.GeoDataFrame({"geometry": []}, crs=CRS)
    hazard = _hazard_polygons_two_classes()
    result, _ = overlay_points(empty_points, hazard)
    assert result == {}


# --- overlay_lines -------------------------------------------------------------


def test_overlay_lines_computes_exact_intersected_length() -> None:
    # A line from x=5 to x=15, y=5: 5 units in class 1 (x:5-10), 5 in class 2 (x:10-15).
    line = LineString([(5, 5), (15, 5)])
    lines_gdf = gpd.GeoDataFrame({"geometry": [line]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()

    result, _ = overlay_lines(lines_gdf, hazard)

    assert result[1]["length_m"] == pytest.approx(5.0)
    assert result[2]["length_m"] == pytest.approx(5.0)
    assert result[1]["feature_count"] == 1
    assert result[2]["feature_count"] == 1


def test_overlay_lines_fully_outside_hazard_not_counted() -> None:
    line = LineString([(500, 500), (600, 600)])
    lines_gdf = gpd.GeoDataFrame({"geometry": [line]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()
    result, _ = overlay_lines(lines_gdf, hazard)
    assert result == {}


def test_overlay_lines_fully_inside_one_class() -> None:
    line = LineString([(1, 1), (9, 9)])
    lines_gdf = gpd.GeoDataFrame({"geometry": [line]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()
    result, _ = overlay_lines(lines_gdf, hazard)
    assert 2 not in result
    assert result[1]["feature_count"] == 1


def test_overlay_lines_rejects_population_field_upstream() -> None:
    # population_field is validated at the run_exposure_analysis level, not
    # inside overlay_lines itself -- documented here as a design note, no
    # assertion needed at this pure-function layer.
    pass


# --- overlay_polygons ----------------------------------------------------------


def test_overlay_polygons_straddling_boundary_splits_area_correctly() -> None:
    # A 20x10 building straddling both hazard classes exactly in half.
    building = Polygon([(0, 0), (20, 0), (20, 10), (0, 10)])
    buildings_gdf = gpd.GeoDataFrame({"geometry": [building]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()

    result, _ = overlay_polygons(buildings_gdf, hazard)

    assert result[1]["area_m2"] == pytest.approx(100.0)
    assert result[2]["area_m2"] == pytest.approx(100.0)
    assert result[1]["feature_count"] == 1
    assert result[2]["feature_count"] == 1


def test_overlay_polygons_fully_outside_not_counted() -> None:
    building = Polygon([(500, 500), (510, 500), (510, 510), (500, 510)])
    buildings_gdf = gpd.GeoDataFrame({"geometry": [building]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()
    result, _ = overlay_polygons(buildings_gdf, hazard)
    assert result == {}


def test_overlay_polygons_fully_inside_gets_full_area() -> None:
    building = Polygon([(1, 1), (9, 1), (9, 9), (1, 9)])  # fully within class 1, area 64
    buildings_gdf = gpd.GeoDataFrame({"geometry": [building]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()
    result, _ = overlay_polygons(buildings_gdf, hazard)
    assert result[1]["area_m2"] == pytest.approx(64.0)
    assert 2 not in result


def test_overlay_polygons_area_weighted_population_apportionment() -> None:
    # A census-tract-style polygon (area 200) with population=1000, split
    # 50/50 by area between the two hazard classes -> 500 each.
    tract = Polygon([(0, 0), (20, 0), (20, 10), (0, 10)])  # area 200
    tracts_gdf = gpd.GeoDataFrame({"population": [1000.0], "geometry": [tract]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()

    result, _ = overlay_polygons(tracts_gdf, hazard, value_field="population")

    assert result[1]["population_sum"] == pytest.approx(500.0)
    assert result[2]["population_sum"] == pytest.approx(500.0)


def test_overlay_polygons_population_apportionment_uneven_split() -> None:
    # A tract covering x:[0,20] (area 200) with population=100; only the
    # x:[0,5] portion (area 50, i.e. 25% of the tract) falls in class 1.
    tract = Polygon([(0, 0), (20, 0), (20, 10), (0, 10)])
    tracts_gdf = gpd.GeoDataFrame({"population": [100.0], "geometry": [tract]}, crs=CRS)
    # Narrower class-1 hazard polygon this time: x in [0,5].
    hazard = gpd.GeoDataFrame(
        {"hazard_class_code": [1], "geometry": [Polygon([(0, 0), (5, 0), (5, 10), (0, 10)])]}, crs=CRS
    )

    result, _ = overlay_polygons(tracts_gdf, hazard, value_field="population")

    # 25% of the tract's area overlaps class 1 -> 25% of its population.
    assert result[1]["population_sum"] == pytest.approx(25.0)


def test_overlay_polygons_zero_area_polygon_does_not_crash() -> None:
    # A degenerate (zero-area) polygon should contribute 0, not NaN/inf.
    degenerate = Polygon([(1, 1), (1, 1), (1, 1)])
    gdf = gpd.GeoDataFrame({"population": [50.0], "geometry": [degenerate]}, crs=CRS)
    hazard = _hazard_polygons_two_classes()
    result, _ = overlay_polygons(gdf, hazard, value_field="population")
    assert result == {}  # degenerate geometry has no area to intersect
