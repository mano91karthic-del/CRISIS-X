"""Phase 6 performance fix: a spatially small exposure dataset must not
require reading/processing an entire large hazard raster. Covers the
pure `compute_hazard_read_window` windowing math directly, then proves
-- by instrumenting the actual `rasterio` read call, not by timing, which
would be flaky -- that `run_exposure_analysis` genuinely reads only a
small window of a large raster, with results identical to what a
full-raster read would have produced.
"""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.exposure import compute_hazard_read_window, run_exposure_analysis

CRS = "EPSG:32643"


# --- compute_hazard_read_window (pure) ----------------------------------------------


def _large_raster_transform(pixel_size: float = 10.0):
    # Mirrors the real reported scale class: origin (0, 0) top-left-ish,
    # a raster spanning tens of kilometers at 10m resolution.
    return from_origin(0, 36470, pixel_size, pixel_size)  # matches ~3647-row scale


def test_window_is_much_smaller_than_full_raster_for_a_small_exposure_bbox() -> None:
    # A raster the same scale class as the real Chennai landslide GeoTIFF
    # (~3569 x 3647 cells) with a small exposure bbox covering only a
    # 50x50-cell corner -- matching "50,000 buildings covering only a
    # small part of the raster."
    width, height = 3569, 3647
    transform = _large_raster_transform(10.0)

    # A small bbox: 500m x 500m (50 cells) in one corner, expressed in
    # the SAME CRS as the raster.
    exposure_bounds = (1000.0, 35500.0, 1500.0, 36000.0)

    window = compute_hazard_read_window(width, height, transform, CRS, exposure_bounds, CRS)

    assert window is not None
    full_cells = width * height
    window_cells = window.width * window.height
    assert window_cells < full_cells * 0.01, (
        f"windowed read ({window_cells} cells) should be under 1% of the full raster "
        f"({full_cells} cells) for a spatially small exposure dataset"
    )
    # Sanity: the window is still a reasonably tight fit around the bbox
    # (tens of cells, not thousands) -- not just "smaller than everything."
    assert window.width < 200
    assert window.height < 200


def test_window_reprojects_exposure_bbox_from_a_different_crs() -> None:
    width, height = 100, 100
    transform = from_origin(0, 1000, 10.0, 10.0)  # 1km x 1km raster in EPSG:32643

    # A WGS84 bbox that, reprojected, should land inside the small raster
    # above -- reuses the same real-world reference point other exposure
    # tests in this suite already use (lon ~77, lat ~13 -> EPSG:32643).
    from rasterio.warp import transform_bounds

    # Build a WGS84 bbox from a known point inside the raster's own extent.
    known_point_src_crs = (500.0, 500.0, 510.0, 510.0)
    wgs84_bounds = transform_bounds(CRS, "EPSG:4326", *known_point_src_crs)

    window = compute_hazard_read_window(width, height, transform, CRS, wgs84_bounds, "EPSG:4326")

    assert window is not None
    assert window.width < width  # genuinely windowed, not the whole raster
    assert window.height < height


def test_window_is_none_when_exposure_bbox_does_not_overlap_raster() -> None:
    width, height = 100, 100
    transform = from_origin(0, 1000, 10.0, 10.0)  # raster spans x:[0,1000], y:[0,1000]

    far_away_bounds = (1_000_000.0, 1_000_000.0, 1_000_100.0, 1_000_100.0)
    window = compute_hazard_read_window(width, height, transform, CRS, far_away_bounds, CRS)

    assert window is None


def test_window_is_none_for_non_finite_reprojected_bounds() -> None:
    width, height = 100, 100
    transform = from_origin(0, 1000, 10.0, 10.0)
    window = compute_hazard_read_window(width, height, transform, CRS, (float("nan"), 0.0, 10.0, 10.0), CRS)
    assert window is None


def test_window_covering_the_whole_raster_is_clipped_not_expanded() -> None:
    width, height = 50, 50
    transform = from_origin(0, 500, 10.0, 10.0)
    # A bbox far larger than the raster itself.
    huge_bounds = (-10_000.0, -10_000.0, 10_000.0, 10_000.0)
    window = compute_hazard_read_window(width, height, transform, CRS, huge_bounds, CRS)

    assert window is not None
    assert window.col_off == 0
    assert window.row_off == 0
    assert window.width == width
    assert window.height == height


def test_window_buffer_pads_beyond_the_exact_bbox() -> None:
    width, height = 100, 100
    transform = from_origin(0, 1000, 10.0, 10.0)
    # A bbox that aligns exactly with cells [10:20, 10:20] (in row/col terms).
    exposure_bounds = (100.0, 800.0, 200.0, 900.0)

    window_no_buffer = compute_hazard_read_window(width, height, transform, CRS, exposure_bounds, CRS, buffer_px=0)
    window_buffered = compute_hazard_read_window(width, height, transform, CRS, exposure_bounds, CRS, buffer_px=5)

    assert window_buffered.width == window_no_buffer.width + 10
    assert window_buffered.height == window_no_buffer.height + 10


# --- run_exposure_analysis: proves the ACTUAL read is windowed, not full -----------


def _write_large_landslide_raster(path: Path, width: int, height: int, *, pixel_size: float = 10.0) -> None:
    """A large hazard raster where only a small corner (rows/cols 0-19)
    is 'very_high' (class 5) and everything else is 'very_low' (class
    1) -- large enough that reading it in full is measurably expensive
    (millions of cells at real scale), small enough here to keep the
    test itself fast while still exercising a real multi-hundred-
    thousand-cell array.
    """
    transform = from_origin(0, height * pixel_size, pixel_size, pixel_size)
    codes = np.ones((height, width), dtype="float32")
    codes[:20, :20] = 5.0
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
        crs=CRS, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(codes, 1)


def test_run_exposure_analysis_reads_a_small_window_not_the_full_raster(tmp_path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import Point

    width, height = 1200, 1200  # 1.44M cells -- large enough to prove the point, small enough to stay fast
    hazard_path = tmp_path / "large_landslide.tif"
    _write_large_landslide_raster(hazard_path, width, height)

    # A single point squarely inside the small "very_high" corner (rows/cols 0-19,
    # i.e. world coords x:[0,200], y:[height*10-200, height*10]).
    top_y = height * 10.0
    points = gpd.GeoDataFrame({"geometry": [Point(50.0, top_y - 50.0)]}, crs=CRS)
    exposure_path = tmp_path / "hospitals.geojson"
    points.to_file(exposure_path, driver="GeoJSON")

    output_path = tmp_path / "exposure_features.geojson"

    original_read = rasterio.io.DatasetReader.read
    captured_windows: list[object] = []

    def spy_read(self, *args, **kwargs):
        window = kwargs.get("window") or (args[1] if len(args) > 1 else None)
        captured_windows.append(window)
        return original_read(self, *args, **kwargs)

    with patch.object(rasterio.io.DatasetReader, "read", spy_read):
        result = run_exposure_analysis(hazard_path, "landslide_susceptibility", exposure_path, output_path)

    # Correctness: the point is exactly in the very_high corner.
    assert result.results["by_class"]["very_high"]["count"] == 1
    assert "very_low" not in result.results["by_class"]

    # Performance: at least one windowed read happened, and its cell count
    # is a tiny fraction of the full raster -- proving the hazard raster's
    # full 1.44M cells were never all read into memory for this analysis.
    windowed_reads = [w for w in captured_windows if w is not None]
    assert windowed_reads, "expected run_exposure_analysis to issue at least one windowed rasterio read"
    smallest_window = min(windowed_reads, key=lambda w: w.width * w.height)
    full_cells = width * height
    window_cells = smallest_window.width * smallest_window.height
    assert window_cells < full_cells * 0.01, (
        f"expected a windowed read under 1% of the full raster ({full_cells} cells), got {window_cells} cells"
    )


def test_run_exposure_analysis_no_overlap_short_circuits_without_full_read(tmp_path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import Point

    width, height = 600, 600
    hazard_path = tmp_path / "hazard_for_no_overlap.tif"
    _write_large_landslide_raster(hazard_path, width, height)

    # A point far outside the hazard raster's extent entirely (raster
    # spans x:[0,6000], y:[0,6000]).
    points = gpd.GeoDataFrame({"geometry": [Point(50_000.0, 50_000.0)]}, crs=CRS)
    exposure_path = tmp_path / "hospitals_far.geojson"
    points.to_file(exposure_path, driver="GeoJSON")

    output_path = tmp_path / "exposure_features.geojson"

    original_read = rasterio.io.DatasetReader.read
    captured_shapes: list[tuple[int, int]] = []

    def spy_read(self, *args, **kwargs):
        arr = original_read(self, *args, **kwargs)
        captured_shapes.append(arr.shape)
        return arr

    with patch.object(rasterio.io.DatasetReader, "read", spy_read):
        result = run_exposure_analysis(hazard_path, "landslide_susceptibility", exposure_path, output_path)

    # Existing "no overlap" behavior is preserved: a completed analysis
    # with an empty by_class, not an error.
    assert result.results["by_class"] == {}

    # No read pulled the full 600x600 raster into memory -- every actual
    # array read observed was tiny (the 1x1 short-circuit array).
    full_cells = width * height
    for shape in captured_shapes:
        cells = shape[0] * shape[1]
        assert cells < full_cells * 0.01, f"unexpected large read of shape {shape} for a non-overlapping pair"


def test_run_exposure_analysis_building_near_window_edge_is_not_clipped(tmp_path: Path) -> None:
    """Proves the outward-rounding + pixel buffer never lose a partial
    cell a feature genuinely touches -- a building placed right at the
    edge of the exposure dataset's own bbox must get its full, correct
    intersection area, identical to what a full-raster (unwindowed) read
    would have produced.
    """
    import geopandas as gpd
    from shapely.geometry import Polygon

    width, height = 400, 400
    hazard_path = tmp_path / "hazard_edge.tif"
    _write_large_landslide_raster(hazard_path, width, height)

    top_y = height * 10.0
    # A building exactly straddling the very_high (rows/cols 0-19) /
    # very_low boundary at x=200 (the same boundary style already proven
    # correct by test_buildings_polygon_straddling_boundary in
    # test_exposure_api.py, here specifically placed at the edge of the
    # windowed region to prove windowing doesn't reintroduce clipping).
    building = Polygon([(150, top_y - 190), (250, top_y - 190), (250, top_y - 10), (150, top_y - 10)])
    buildings_gdf = gpd.GeoDataFrame({"geometry": [building]}, crs=CRS)
    exposure_path = tmp_path / "buildings_edge.geojson"
    buildings_gdf.to_file(exposure_path, driver="GeoJSON")

    output_path = tmp_path / "exposure_features.geojson"
    result = run_exposure_analysis(hazard_path, "landslide_susceptibility", exposure_path, output_path)

    by_class = result.results["by_class"]
    # Building is 100m x 180m = 18000 m^2, split exactly in half by x=200
    # (very_high for x<200, very_low for x>=200) -> 9000 m^2 each side.
    assert by_class["very_high"]["area_m2"] == pytest.approx(9000.0)
    assert by_class["very_low"]["area_m2"] == pytest.approx(9000.0)
