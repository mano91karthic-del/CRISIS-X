"""API-level integration tests for the Phase 11 preview endpoints:
GET /datasets/{id}/geojson, /preview.png, /preview-bounds,
/heightmap.png, /heightmap-bounds -- fail-closed coverage plus happy
paths against real (tiny, synthetic) raster/vector files.
"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin


def _create_project(client: TestClient, name: str = "Preview Test Project") -> str:
    return client.post("/projects", json={"name": name}).json()["id"]


def _upload_raster(client: TestClient, project_id: str, path: Path, dataset_type: str, filename: str) -> dict:
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": dataset_type},
            files={"file": (filename, f, "image/tiff")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_dem(tmp_path: Path, name: str, crs: str = "EPSG:32643") -> Path:
    rows, cols = 8, 8
    transform = from_origin(0, 80, 10, 10)
    elevation = np.arange(rows * cols, dtype="float32").reshape(rows, cols)
    path = tmp_path / f"dem_{name}.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
        crs=crs, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)
    return path


def _make_classified_raster(tmp_path: Path, name: str) -> Path:
    rows, cols = 6, 6
    transform = from_origin(0, 60, 10, 10)
    classes = np.ones((rows, cols), dtype="float32")
    classes[3:, 3:] = 3.0
    path = tmp_path / f"classified_{name}.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
        crs="EPSG:32643", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(classes, 1)
    return path


def _upload_roads(client: TestClient, project_id: str, tmp_path: Path, n_features: int = 2) -> dict:
    import geopandas as gpd
    from shapely.geometry import LineString

    lines = [LineString([(i * 10, 0), (i * 10 + 5, 5)]) for i in range(n_features)]
    gdf = gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:32643")
    path = tmp_path / "roads.geojson"
    gdf.to_file(path, driver="GeoJSON")
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "roads"},
            files={"file": ("roads.geojson", f, "application/geo+json")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- /geojson -----------------------------------------------------------------------


def test_geojson_endpoint_returns_wgs84_envelope(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    roads = _upload_roads(client, project_id, tmp_path, n_features=3)

    resp = client.get(f"/datasets/{roads['id']}/geojson")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 3
    assert body["truncated"] is False
    assert body["total_feature_count"] == 3
    lon, lat = body["features"][0]["geometry"]["coordinates"][0]
    assert -180 <= lon <= 180 and -90 <= lat <= 90


def test_geojson_endpoint_respects_limit_and_flags_truncation(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    roads = _upload_roads(client, project_id, tmp_path, n_features=5)

    resp = client.get(f"/datasets/{roads['id']}/geojson", params={"limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["features"]) == 2
    assert body["truncated"] is True
    assert body["total_feature_count"] == 5


def test_geojson_endpoint_rejects_raster_dataset(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "notvec"), "dem", "dem.tif")

    resp = client.get(f"/datasets/{dem['id']}/geojson")
    assert resp.status_code == 400


def test_geojson_endpoint_missing_dataset_404s(client: TestClient) -> None:
    assert client.get("/datasets/does-not-exist/geojson").status_code == 404


# --- /preview.png + /preview-bounds -------------------------------------------------


def test_preview_png_returns_image_with_correct_content_type(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "png"), "dem", "dem.tif")

    resp = client.get(f"/datasets/{dem['id']}/preview.png")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes


def test_preview_png_downsamples_to_max_dim(client: TestClient, tmp_path: Path) -> None:
    from PIL import Image
    import io

    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "downsample"), "dem", "dem.tif")

    resp = client.get(f"/datasets/{dem['id']}/preview.png", params={"max_dim": 4})
    assert resp.status_code == 200
    img = Image.open(io.BytesIO(resp.content))
    assert max(img.size) <= 4


def test_preview_png_classified_raster_uses_legend_colors(client: TestClient, tmp_path: Path) -> None:
    """Uses the REAL Phase 4 landslide hazard pipeline (not a bare
    upload) so the output Dataset carries a genuine
    metadata_json["class_legend"], exactly as it would in production --
    a bare upload never has one, so this can't be exercised with a
    directly-uploaded raster.
    """
    from PIL import Image
    import io

    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "cls"), "dem", "dem.tif")
    slope_resp = client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})
    assert slope_resp.status_code == 201, slope_resp.text

    scenario_resp = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": "Preview test landslide", "dem_dataset_id": dem["id"]},
    )
    assert scenario_resp.status_code == 201, scenario_resp.text
    landslide = scenario_resp.json()["datasets"][0]
    assert landslide["metadata_json"].get("class_legend")

    resp = client.get(f"/datasets/{landslide['id']}/preview.png")
    assert resp.status_code == 200, resp.text
    img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
    pixels = set(img.getdata())
    # every opaque pixel must be one of the legend's own colors -- never
    # an invented intermediate color from averaging class codes.
    from app.services.preview import LEGEND_COLORS, _hex_to_rgb

    allowed = {(*_hex_to_rgb(LEGEND_COLORS[label]), 255) for label in landslide["metadata_json"]["class_legend"].values()}
    opaque_pixels = {p for p in pixels if p[3] == 255}
    assert opaque_pixels <= allowed, f"unexpected colors in classified preview: {opaque_pixels - allowed}"


def test_preview_bounds_returns_wgs84_bbox(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "bounds"), "dem", "dem.tif")

    resp = client.get(f"/datasets/{dem['id']}/preview-bounds")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert -180 <= body["west"] <= body["east"] <= 180
    assert -90 <= body["south"] <= body["north"] <= 90
    assert body["reprojection_applied"] is True  # source was EPSG:32643
    assert body["native_crs"]


def test_preview_endpoints_reject_vector_dataset(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    roads = _upload_roads(client, project_id, tmp_path)

    assert client.get(f"/datasets/{roads['id']}/preview.png").status_code == 400
    assert client.get(f"/datasets/{roads['id']}/preview-bounds").status_code == 400


def test_preview_endpoints_missing_dataset_404s(client: TestClient) -> None:
    assert client.get("/datasets/does-not-exist/preview.png").status_code == 404
    assert client.get("/datasets/does-not-exist/preview-bounds").status_code == 404


def test_preview_endpoint_stored_file_missing_returns_410(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "gone"), "dem", "dem.tif")
    # storage_path isn't exposed on DatasetRead -- delete via the dataset's known on-disk storage directory instead.
    import app.services.storage as storage_module

    stored_dir = storage_module.dataset_storage_dir(project_id, dem["id"])
    for f in stored_dir.iterdir():
        f.unlink()

    resp = client.get(f"/datasets/{dem['id']}/preview.png")
    assert resp.status_code == 410


# --- /heightmap.png + /heightmap-bounds ---------------------------------------------


def test_heightmap_png_returns_grayscale_image(client: TestClient, tmp_path: Path) -> None:
    from PIL import Image
    import io

    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "hm"), "dem", "dem.tif")

    resp = client.get(f"/datasets/{dem['id']}/heightmap.png")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(resp.content))
    assert img.mode == "L"


def test_heightmap_bounds_returns_native_crs_and_elevation_range(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_dem(tmp_path, "hmbounds"), "dem", "dem.tif")

    resp = client.get(f"/datasets/{dem['id']}/heightmap-bounds")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["native_crs"] == "EPSG:32643"
    assert body["reprojection_applied"] is False  # native CRS, never reprojected
    assert body["elevation_min_m"] is not None
    assert body["elevation_max_m"] is not None
    assert body["elevation_max_m"] > body["elevation_min_m"]
    assert body["pixel_size_x_m"] == 10.0
    # bounds must match the known DEM extent exactly (0,0)-(80,80), in
    # its own declared native CRS -- never reprojected.
    assert body["west"] == pytest.approx(0.0)
    assert body["north"] == pytest.approx(80.0)


def _make_geographic_dem(tmp_path: Path, name: str) -> Path:
    """An unprojected DEM (EPSG:4326, degree-based pixel spacing) --
    matches the real-world case that triggered the Phase 11 "3D terrain
    invisible" bug: an SRTM-style tile uploaded without reprojection.
    """
    rows, cols = 8, 8
    degree_size = 1.0 / 3600.0  # 1 arc-second, same as the real Chennai SRTM tile
    transform = from_origin(80.0, 14.0, degree_size, degree_size)
    elevation = np.arange(rows * cols, dtype="float32").reshape(rows, cols)
    path = tmp_path / f"geo_dem_{name}.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
        crs="EPSG:4326", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)
    return path


def test_heightmap_bounds_reports_real_meters_for_geographic_dem(client: TestClient, tmp_path: Path) -> None:
    """Phase 11 regression: a geographic-CRS DEM must never have its
    degree-sized pixel spacing (~0.0002778) reported as pixel_size_x_m --
    that's exactly what shrank a ~111km-wide real DEM into a ~1-meter
    Three.js mesh. Reused end to end by the frontend camera-framing fix.
    """
    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_geographic_dem(tmp_path, "hmgeo"), "dem", "dem_geo.tif")

    resp = client.get(f"/datasets/{dem['id']}/heightmap-bounds")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["native_crs"] == "EPSG:4326"
    assert body["reprojection_applied"] is False  # array/CRS itself is still never reprojected
    # Nowhere near the raw degree value (~0.0002778) -- must be real meters.
    assert body["pixel_size_x_m"] > 1.0
    assert 25.0 < body["pixel_size_x_m"] < 35.0
    assert 25.0 < body["pixel_size_y_m"] < 35.0

    heightmap_resp = client.get(f"/datasets/{dem['id']}/heightmap.png")
    assert heightmap_resp.status_code == 200, heightmap_resp.text

    # End-to-end sanity matching the bug: 8 pixels at a real ~30m/pixel
    # implies a physically plausible ~240m-wide DEM, not ~0.0022m.
    implied_world_width_m = 8 * body["pixel_size_x_m"]
    assert 100.0 < implied_world_width_m < 500.0


def _make_large_geographic_dem(tmp_path: Path, name: str, *, height: int = 1100, width: int = 1300) -> Path:
    """A DEM larger than DEFAULT_MAX_PREVIEW_DIM (1024) per side, matching
    the real Chennai SRTM tile's scale (3601x3601) enough to actually
    exercise build_heightmap()'s downsampling path -- the case the
    heightmap-bounds/heightmap.png grid-consistency fix targets.
    """
    degree_size = 1.0 / 3600.0
    transform = from_origin(80.0, 14.0, degree_size, degree_size)
    elevation = np.linspace(0, 50, height * width, dtype="float32").reshape(height, width)
    path = tmp_path / f"large_geo_dem_{name}.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
        crs="EPSG:4326", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)
    return path


def test_heightmap_bounds_and_png_agree_on_pixel_grid_for_raster_larger_than_max_dim(client: TestClient, tmp_path: Path) -> None:
    """Regression for the heightmap-bounds/heightmap.png downsampling
    inconsistency: for a DEM exceeding DEFAULT_MAX_PREVIEW_DIM per side,
    /heightmap-bounds must report the pixel size/bounds of the SAME grid
    /heightmap.png actually encodes, not the raster's native resolution.
    """
    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_large_geographic_dem(tmp_path, "big"), "dem", "dem_big.tif")

    bounds_resp = client.get(f"/datasets/{dem['id']}/heightmap-bounds")
    assert bounds_resp.status_code == 200, bounds_resp.text
    bounds_body = bounds_resp.json()

    png_resp = client.get(f"/datasets/{dem['id']}/heightmap.png")
    assert png_resp.status_code == 200, png_resp.text

    from PIL import Image
    import io

    png = Image.open(io.BytesIO(png_resp.content))
    png_width, png_height = png.size

    assert max(png.size) <= 1024  # actually downsampled, per DEFAULT_MAX_PREVIEW_DIM
    assert png_width < 1300 and png_height < 1100  # smaller than the native 1300x1100 grid

    # The total geographic extent (degrees) is unaffected by downsampling --
    # only the pixel COUNT changes -- so this is a stable sanity check that
    # bounds themselves are still correct: width == native_pixel_count *
    # native_degree_size (1 arc-second).
    degree_size = 1.0 / 3600.0
    assert (bounds_body["east"] - bounds_body["west"]) == pytest.approx(1300 * degree_size, rel=0.01)
    assert (bounds_body["north"] - bounds_body["south"]) == pytest.approx(1100 * degree_size, rel=0.01)

    # Still real meters (never degrees), AND measurably coarser than the
    # ~25-35m/pixel a NATIVE-resolution 1-arcsecond DEM reports (see
    # test_heightmap_bounds_reports_real_meters_for_geographic_dem) --
    # proving the downsampled (not native) grid's pixel size is what gets
    # reported, matching what /heightmap.png actually encoded.
    assert bounds_body["pixel_size_x_m"] > 35.0
    assert bounds_body["pixel_size_x_m"] < 100.0


def test_heightmap_bounds_max_dim_query_param_matches_heightmap_png(client: TestClient, tmp_path: Path) -> None:
    """A caller passing a non-default max_dim to /heightmap.png must be
    able to pass the same value to /heightmap-bounds and get metadata
    describing that exact resolution.
    """
    project_id = _create_project(client)
    dem = _upload_raster(client, project_id, _make_large_geographic_dem(tmp_path, "customdim"), "dem", "dem_customdim.tif")

    bounds_resp = client.get(f"/datasets/{dem['id']}/heightmap-bounds", params={"max_dim": 256})
    assert bounds_resp.status_code == 200, bounds_resp.text
    bounds_body = bounds_resp.json()

    png_resp = client.get(f"/datasets/{dem['id']}/heightmap.png", params={"max_dim": 256})
    assert png_resp.status_code == 200, png_resp.text

    from PIL import Image
    import io

    png = Image.open(io.BytesIO(png_resp.content))
    assert max(png.size) <= 256

    # Same stable degrees-extent sanity check as above, now at a much
    # coarser max_dim -- proves the endpoint isn't just hardcoded to the
    # DEFAULT_MAX_PREVIEW_DIM=1024 case.
    degree_size = 1.0 / 3600.0
    assert (bounds_body["east"] - bounds_body["west"]) == pytest.approx(1300 * degree_size, rel=0.01)
    # At max_dim=256 the pixel size must be coarser still than the
    # max_dim=1024 case (~38-90m band asserted above).
    assert bounds_body["pixel_size_x_m"] > 100.0


def test_heightmap_endpoints_reject_non_dem_dataset(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    with _make_classified_raster(tmp_path, "notdem").open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": "landslide_susceptibility_screening"},
            files={"file": ("classified.tif", f, "image/tiff")},
        )
    dataset = resp.json()

    assert client.get(f"/datasets/{dataset['id']}/heightmap.png").status_code == 400
    assert client.get(f"/datasets/{dataset['id']}/heightmap-bounds").status_code == 400
