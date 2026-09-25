"""Pure-function tests for Phase 11 preview conversion: colorization,
GeoJSON coordinate rounding/truncation, and legend-label coverage --
verified against known synthetic cases, same discipline as
test_exposure.py/test_risk.py.
"""

import json

import numpy as np
import pytest

from app.services.preview import (
    DEFAULT_MAX_PREVIEW_DIM,
    LEGEND_COLORS,
    _geographic_pixel_size_m,
    _heightmap_output_grid,
    _hex_to_rgb,
    _round_coordinates,
    colorize_array,
)


# --- LEGEND_COLORS coverage -------------------------------------------------------


def test_legend_colors_covers_every_fixed_label_vocabulary() -> None:
    from app.services.exposure import EO_CHANGE_MASK_LABELS, FLOOD_LABELS
    from app.services.hazard_landslide import CLASS_LEGEND
    from app.services.risk import RISK_CLASS_LEGEND

    all_labels = (
        set(CLASS_LEGEND.values())
        | set(FLOOD_LABELS.values())
        | set(EO_CHANGE_MASK_LABELS.values())
        | set(RISK_CLASS_LEGEND.values())
    )
    missing = all_labels - set(LEGEND_COLORS)
    assert missing == set(), f"LEGEND_COLORS is missing colors for: {missing}"


def test_legend_colors_are_valid_hex() -> None:
    for label, color in LEGEND_COLORS.items():
        assert color.startswith("#") and len(color) == 7, f"{label} -> {color} is not a #RRGGBB hex color"
        _hex_to_rgb(color)  # must not raise


# --- colorize_array ----------------------------------------------------------------


def test_colorize_array_classified_maps_known_codes_to_legend_colors() -> None:
    array = np.array([[1.0, 2.0], [3.0, -9999.0]])
    rgba = colorize_array(array, nodata=-9999.0, class_legend={"1": "very_low", "2": "moderate", "3": "very_high"})
    assert tuple(rgba[0, 0]) == (*_hex_to_rgb(LEGEND_COLORS["very_low"]), 255)
    assert tuple(rgba[0, 1]) == (*_hex_to_rgb(LEGEND_COLORS["moderate"]), 255)
    assert tuple(rgba[1, 0]) == (*_hex_to_rgb(LEGEND_COLORS["very_high"]), 255)
    assert rgba[1, 1, 3] == 0  # nodata -> fully transparent


def test_colorize_array_classified_unknown_label_falls_back_never_crashes() -> None:
    array = np.array([[7.0]])
    rgba = colorize_array(array, nodata=None, class_legend={"7": "totally_unrecognized_label"})
    assert rgba[0, 0, 3] == 255  # still opaque -- rendered, just with the fallback color
    from app.services.preview import UNKNOWN_LABEL_COLOR

    assert tuple(rgba[0, 0, :3]) == _hex_to_rgb(UNKNOWN_LABEL_COLOR)


def test_colorize_array_continuous_normalizes_min_max() -> None:
    array = np.array([[0.0, 50.0], [100.0, np.nan]])
    rgba = colorize_array(array, nodata=None, class_legend=None)
    assert rgba[1, 1, 3] == 0  # NaN -> transparent
    assert rgba[0, 0, 3] == 255
    # min and max should land on the ramp's two end colors.
    from app.services.preview import _CONTINUOUS_RAMP_STOPS

    assert tuple(rgba[0, 0, :3]) == _CONTINUOUS_RAMP_STOPS[0][1]
    assert tuple(rgba[1, 0, :3]) == _CONTINUOUS_RAMP_STOPS[-1][1]


def test_colorize_array_continuous_all_nodata_returns_fully_transparent() -> None:
    array = np.full((2, 2), np.nan)
    rgba = colorize_array(array, nodata=None, class_legend=None)
    assert np.all(rgba[:, :, 3] == 0)


# --- _round_coordinates ------------------------------------------------------------


def test_round_coordinates_point() -> None:
    assert _round_coordinates([1.123456789, 2.987654321], 3) == [1.123, 2.988]


def test_round_coordinates_nested_linestring() -> None:
    coords = [[1.123456, 2.654321], [3.111111, 4.222222]]
    result = _round_coordinates(coords, 2)
    assert result == [[1.12, 2.65], [3.11, 4.22]]


def test_round_coordinates_leaves_non_float_untouched() -> None:
    assert _round_coordinates("not-a-number", 2) == "not-a-number"
    assert _round_coordinates(5, 2) == 5


# --- build_geojson_preview (needs a real file, still a pure-ish transform test) ----


def test_build_geojson_preview_truncates_and_reports_envelope(tmp_path) -> None:
    import geopandas as gpd
    from shapely.geometry import Point

    from app.services.preview import build_geojson_preview

    points = [Point(x, x) for x in range(5)]
    gdf = gpd.GeoDataFrame({"id": range(5)}, geometry=points, crs="EPSG:4326")
    path = tmp_path / "points.geojson"
    gdf.to_file(path, driver="GeoJSON")

    result = build_geojson_preview(path, limit=3, precision=6)
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) == 3
    assert result["truncated"] is True
    assert result["total_feature_count"] == 5
    assert result["source_crs"]


def test_build_geojson_preview_reprojects_to_wgs84(tmp_path) -> None:
    import geopandas as gpd
    from shapely.geometry import Point

    from app.services.preview import build_geojson_preview

    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[Point(500000, 5000000)], crs="EPSG:32643")
    path = tmp_path / "utm_point.geojson"
    gdf.to_file(path, driver="GeoJSON")

    result = build_geojson_preview(path)
    lon, lat = result["features"][0]["geometry"]["coordinates"]
    assert -180 <= lon <= 180
    assert -90 <= lat <= 90
    assert result["source_crs"] != "EPSG:4326"


def test_build_geojson_preview_rejects_unreadable_file(tmp_path) -> None:
    # GeoJSON's driver defaults to WGS84 per RFC7946 even without an
    # explicit CRS, so "no CRS" isn't a reachable failure mode for this
    # format (same reason validation.py's own vector check never rejects
    # a bare-CRS GeoJSON) -- the fail-closed path is instead exercised
    # here via a file that simply isn't valid vector data at all.
    from app.services.preview import UnprojectableDatasetError, build_geojson_preview

    path = tmp_path / "not_really_geojson.geojson"
    path.write_text("this is not valid geojson")

    with pytest.raises(UnprojectableDatasetError):
        build_geojson_preview(path)


# --- _geographic_pixel_size_m (Phase 11 3D-terrain-invisible bug fix) --------------
#
# A DEM uploaded in a geographic CRS (degrees, e.g. an unprojected SRTM
# tile) must never have its raw degree-sized affine pixel spacing
# reported as if it were meters -- see
# docs/architecture/0012-phase-11-command-dashboard.md and the bug this
# fixes: a ~111km-wide real DEM was being built as a ~1-meter-wide
# Three.js mesh because pixel_size_x_m held a degree value (~0.0002778)
# instead of meters.


def test_geographic_pixel_size_m_at_equator_matches_known_geodesic_constant() -> None:
    # 1 degree of longitude at the equator is a well-known WGS84 geodesic
    # constant, ~111,320m -- so a 1-arcsecond (1/3600 degree) pixel there
    # should convert to ~30.92m, nowhere near the raw degree value.
    transform_degree_size = 1.0 / 3600.0
    from rasterio.transform import from_origin

    transform = from_origin(80.0, 0.0005, transform_degree_size, transform_degree_size)
    bounds = (80.0, 0.0, 80.001, 0.0005)  # centered essentially at the equator

    x_m, y_m = _geographic_pixel_size_m(transform, bounds)

    assert x_m == pytest.approx(111_320 / 3600, rel=0.01)
    assert y_m == pytest.approx(111_320 / 3600, rel=0.01)
    # Sanity: nowhere near the raw (wrong) degree-sized value.
    assert x_m > 1.0


def test_geographic_pixel_size_m_is_latitude_aware_not_a_flat_approximation() -> None:
    # Longitude spacing shrinks toward the poles (meridian convergence);
    # latitude spacing stays ~constant. A correct, latitude-aware
    # conversion must reflect that asymmetry -- a naive flat multiply by
    # a single constant (e.g. degrees * 111320) would NOT.
    from rasterio.transform import from_origin

    degree_size = 1.0 / 3600.0
    transform = from_origin(80.0, 60.0005, degree_size, degree_size)

    equator_bounds = (80.0, 0.0, 80.001, 0.0005)
    high_lat_bounds = (80.0, 60.0, 80.001, 60.0005)

    eq_x_m, eq_y_m = _geographic_pixel_size_m(transform, equator_bounds)
    hl_x_m, hl_y_m = _geographic_pixel_size_m(transform, high_lat_bounds)

    # Longitude (x) spacing at 60 degrees latitude is roughly cos(60deg)=0.5
    # of its equatorial value -- a real geodesic effect, not noise.
    assert hl_x_m < eq_x_m * 0.6
    assert hl_x_m == pytest.approx(eq_x_m * 0.5, rel=0.05)
    # Latitude (y) spacing barely changes with latitude (WGS84 ellipsoid
    # flattening causes only a tiny variation) -- must stay close.
    assert hl_y_m == pytest.approx(eq_y_m, rel=0.01)


def test_geographic_pixel_size_m_handles_southern_hemisphere() -> None:
    from rasterio.transform import from_origin

    degree_size = 1.0 / 3600.0
    transform = from_origin(80.0, -13.9995, degree_size, degree_size)
    bounds = (80.0, -14.0, 80.001, -13.9995)  # Chennai-latitude magnitude, southern hemisphere

    x_m, y_m = _geographic_pixel_size_m(transform, bounds)

    assert x_m > 0
    assert y_m > 0
    # Same magnitude as the real Chennai-latitude case (~13.5N), since the
    # geodesic conversion depends on |latitude|, not its sign.
    assert x_m == pytest.approx(30.1, abs=1.0)


# --- compute_heightmap_bounds / build_heightmap: CRS-dependent pixel size ----------


def _write_geographic_dem(path, *, origin_lon: float = 80.0, origin_lat: float = 14.0, pixels: int = 8) -> None:
    import rasterio
    from rasterio.transform import from_origin

    degree_size = 1.0 / 3600.0  # 1 arc-second, matching the real SRTM tile that triggered this bug
    transform = from_origin(origin_lon, origin_lat, degree_size, degree_size)
    elevation = np.linspace(0, 50, pixels * pixels, dtype="float32").reshape(pixels, pixels)
    with rasterio.open(
        path, "w", driver="GTiff", height=pixels, width=pixels, count=1, dtype="float32",
        crs="EPSG:4326", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)


def _write_projected_dem(path, *, pixel_size: float = 10.0, pixels: int = 8) -> None:
    import rasterio
    from rasterio.transform import from_origin

    transform = from_origin(0, pixels * pixel_size, pixel_size, pixel_size)
    elevation = np.linspace(0, 50, pixels * pixels, dtype="float32").reshape(pixels, pixels)
    with rasterio.open(
        path, "w", driver="GTiff", height=pixels, width=pixels, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)


def test_compute_heightmap_bounds_reports_meters_not_degrees_for_geographic_dem(tmp_path) -> None:
    from app.services.preview import compute_heightmap_bounds

    path = tmp_path / "geographic_dem.tif"
    _write_geographic_dem(path)

    info = compute_heightmap_bounds(path)

    # The raw degree value would be ~0.0002778 -- assert we are nowhere
    # near that, and instead in a real, physically-plausible meter range
    # for a 1-arcsecond pixel (roughly 28-31m depending on latitude).
    assert info.pixel_size_x_m > 1.0
    assert 25.0 < info.pixel_size_x_m < 35.0
    assert 25.0 < info.pixel_size_y_m < 35.0
    assert info.native_crs.upper().replace(" ", "") in ("EPSG:4326", "EPSG4326") or "4326" in info.native_crs


def test_compute_heightmap_bounds_preserves_behavior_for_projected_crs(tmp_path) -> None:
    from app.services.preview import compute_heightmap_bounds

    path = tmp_path / "projected_dem.tif"
    _write_projected_dem(path, pixel_size=10.0)

    info = compute_heightmap_bounds(path)

    # Already metric -- must be reported EXACTLY as-is (no geodesic
    # conversion applied), same as before this fix.
    assert info.pixel_size_x_m == pytest.approx(10.0)
    assert info.pixel_size_y_m == pytest.approx(10.0)


def test_compute_heightmap_bounds_elevation_semantics_unaffected_by_crs_fix(tmp_path) -> None:
    from app.services.preview import compute_heightmap_bounds

    geo_path = tmp_path / "geographic_dem_elev.tif"
    _write_geographic_dem(geo_path)
    info = compute_heightmap_bounds(geo_path)

    # linspace(0, 50, ...) -- elevation range is untouched by the pixel-size fix.
    assert info.elevation_min_m == pytest.approx(0.0, abs=0.1)
    assert info.elevation_max_m == pytest.approx(50.0, abs=0.1)


def test_build_heightmap_reports_meters_not_degrees_for_geographic_dem(tmp_path) -> None:
    from app.services.preview import build_heightmap

    path = tmp_path / "geographic_dem_hm.tif"
    _write_geographic_dem(path)

    result = build_heightmap(path)

    assert result.pixel_size_x_m > 1.0
    assert 25.0 < result.pixel_size_x_m < 35.0


def test_build_heightmap_preserves_behavior_for_projected_crs(tmp_path) -> None:
    from app.services.preview import build_heightmap

    path = tmp_path / "projected_dem_hm.tif"
    _write_projected_dem(path, pixel_size=10.0)

    result = build_heightmap(path)

    assert result.pixel_size_x_m == pytest.approx(10.0)
    assert result.pixel_size_y_m == pytest.approx(10.0)


def test_build_heightmap_real_world_width_is_physically_plausible_for_geographic_dem(tmp_path) -> None:
    """End-to-end proof of the actual bug fix: an 8x8-pixel, 1-arcsecond
    geographic DEM's implied real-world width (pixel_count * pixel_size_m)
    must land in a physically sane range (roughly 200-280m for 8 pixels
    at ~30m/pixel) -- not the ~0.0022m the pre-fix degree-as-meters bug
    would have produced.
    """
    from app.services.preview import build_heightmap

    path = tmp_path / "geographic_dem_width.tif"
    _write_geographic_dem(path, pixels=8)

    result = build_heightmap(path)
    implied_world_width_m = 8 * result.pixel_size_x_m

    assert implied_world_width_m > 100.0  # would be ~0.0022m before the fix
    assert implied_world_width_m < 500.0


# --- heightmap-bounds vs heightmap.png: downsampling-consistency fix ---------------
#
# Phase 11 follow-up bug: compute_heightmap_bounds() reported native-
# resolution pixel size/bounds, while build_heightmap() downsamples any
# raster exceeding DEFAULT_MAX_PREVIEW_DIM (1024px/side) -- e.g. the real
# 3601x3601 Chennai DEM -- so /heightmap-bounds described a different
# pixel grid than the PNG /heightmap.png actually encoded. Both now
# derive their transform/pixel-size from the same _heightmap_output_grid()
# helper, so they describe exactly the same grid for the same max_dim.


def _write_large_projected_dem(path, *, pixel_size: float = 1.0, height: int = 1100, width: int = 1300) -> None:
    import rasterio
    from rasterio.transform import from_origin

    transform = from_origin(0, height * pixel_size, pixel_size, pixel_size)
    elevation = np.linspace(0, 50, height * width, dtype="float32").reshape(height, width)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)


def _write_large_geographic_dem(path, *, height: int = 1100, width: int = 1300, origin_lon: float = 80.0, origin_lat: float = 14.0) -> None:
    import rasterio
    from rasterio.transform import from_origin

    degree_size = 1.0 / 3600.0  # 1 arc-second, matching the real SRTM tile
    transform = from_origin(origin_lon, origin_lat, degree_size, degree_size)
    elevation = np.linspace(0, 50, height * width, dtype="float32").reshape(height, width)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
        crs="EPSG:4326", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)


def test_heightmap_output_grid_is_a_no_op_below_max_dim() -> None:
    from rasterio.transform import from_origin

    transform = from_origin(0, 80, 10, 10)
    out_transform, out_height, out_width = _heightmap_output_grid(8, 8, transform, DEFAULT_MAX_PREVIEW_DIM)

    assert (out_height, out_width) == (8, 8)
    assert out_transform == transform


def test_heightmap_output_grid_scales_the_longer_side_to_max_dim() -> None:
    from rasterio.transform import from_origin

    transform = from_origin(0, 2000, 1.0, 1.0)
    out_transform, out_height, out_width = _heightmap_output_grid(2000, 1000, transform, 1024)

    assert out_height == 1024  # the longer side is capped exactly at max_dim
    assert out_width == 512  # the shorter side scales down proportionally
    # Pixel spacing must grow by the same factor the grid shrank by.
    assert out_transform.a == pytest.approx(1000 / 512)
    assert abs(out_transform.e) == pytest.approx(2000 / 1024)


def test_compute_heightmap_bounds_and_build_heightmap_agree_for_large_projected_dem(tmp_path) -> None:
    from app.services.preview import build_heightmap, compute_heightmap_bounds

    path = tmp_path / "large_projected_dem.tif"
    _write_large_projected_dem(path, pixel_size=1.0, height=1100, width=1300)

    info = compute_heightmap_bounds(path)
    result = build_heightmap(path)

    # The two endpoints must describe the exact same (downsampled) pixel grid.
    assert info.pixel_size_x_m == pytest.approx(result.pixel_size_x_m)
    assert info.pixel_size_y_m == pytest.approx(result.pixel_size_y_m)
    assert info.bounds_native == pytest.approx(result.bounds_native)

    # The fix must actually take effect: native pixel size was 1.0m, so a
    # correctly downsampled report must be coarser than that, matching the
    # ~1300/1024 scale factor the PNG was actually built at.
    assert info.pixel_size_x_m == pytest.approx(1300 / 1024, rel=0.01)
    assert info.pixel_size_y_m == pytest.approx(1100 / 866, rel=0.01)

    # The PNG itself must actually be downsampled to <= max_dim per side.
    from PIL import Image
    import io

    png = Image.open(io.BytesIO(result.png_bytes))
    assert max(png.size) <= DEFAULT_MAX_PREVIEW_DIM
    assert png.size == (1024, 866)  # round(1300*1024/1300)=1024, round(1100*1024/1300)=866


def test_compute_heightmap_bounds_and_build_heightmap_agree_for_large_geographic_dem(tmp_path) -> None:
    """The same consistency must hold on the geographic-CRS path (the
    real Chennai DEM's own case: EPSG:4326, 3601x3601) -- pixel size is
    still reported in real meters, never degrees, AND matches the actual
    downsampled PNG grid, not the native-resolution grid.
    """
    from app.services.preview import build_heightmap, compute_heightmap_bounds

    path = tmp_path / "large_geographic_dem.tif"
    _write_large_geographic_dem(path, height=1100, width=1300)

    info = compute_heightmap_bounds(path)
    result = build_heightmap(path)

    assert info.pixel_size_x_m == pytest.approx(result.pixel_size_x_m)
    assert info.pixel_size_y_m == pytest.approx(result.pixel_size_y_m)
    assert info.bounds_native == pytest.approx(result.bounds_native)

    # Still real meters (never degrees), and still latitude-aware.
    assert info.pixel_size_x_m > 1.0
    assert info.pixel_size_x_m < 200.0

    from PIL import Image
    import io

    png = Image.open(io.BytesIO(result.png_bytes))
    assert max(png.size) <= DEFAULT_MAX_PREVIEW_DIM
    assert png.size == (1024, 866)


def test_compute_heightmap_bounds_respects_custom_max_dim_matching_heightmap_png(tmp_path) -> None:
    """A caller requesting a non-default max_dim from /heightmap.png must
    be able to pass the SAME max_dim to /heightmap-bounds and get
    consistent metadata for that resolution too.
    """
    from app.services.preview import build_heightmap, compute_heightmap_bounds

    path = tmp_path / "large_projected_dem_custom.tif"
    _write_large_projected_dem(path, pixel_size=1.0, height=1100, width=1300)

    info = compute_heightmap_bounds(path, max_dim=256)
    result = build_heightmap(path, max_dim=256)

    assert info.pixel_size_x_m == pytest.approx(result.pixel_size_x_m)
    assert info.pixel_size_y_m == pytest.approx(result.pixel_size_y_m)

    from PIL import Image
    import io

    png = Image.open(io.BytesIO(result.png_bytes))
    assert max(png.size) <= 256


def test_build_heightmap_elevation_semantics_unaffected_by_downsampling_fix(tmp_path) -> None:
    """elevation_min_m/elevation_max_m must remain exactly as before this
    fix -- this change only touches pixel-size/bounds/transform math, never
    elevation value computation.
    """
    from app.services.preview import build_heightmap

    path = tmp_path / "large_projected_dem_elev.tif"
    _write_large_projected_dem(path, pixel_size=1.0, height=1100, width=1300)

    result = build_heightmap(path)

    assert result.elevation_min_m == pytest.approx(0.0, abs=1.0)
    assert result.elevation_max_m == pytest.approx(50.0, abs=1.0)
