"""API-level integration tests for the Phase 8 routing endpoints: real
road-graph construction, hazard overlay, blocking, and Dijkstra routing
against a real Phase 4 hazard scenario (and, for risk-aware tests, a real
Phase 6 exposure analysis + Phase 7 risk analysis), plus validation and
failure-path coverage.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point

CRS = "EPSG:32643"
_TO_WGS84 = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)


def _lonlat(x: float, y: float) -> dict:
    lon, lat = _TO_WGS84.transform(x, y)
    return {"lon": lon, "lat": lat}


# --- fixture helpers ---------------------------------------------------------


def _create_project(client: TestClient) -> str:
    return client.post("/projects", json={"name": "Routing Test Project"}).json()["id"]


def _upload_raster(client: TestClient, project_id: str, path: Path, dataset_type: str, filename: str) -> dict:
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": dataset_type},
            files={"file": (filename, f, "image/tiff")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload_vector(client: TestClient, project_id: str, gdf: "gpd.GeoDataFrame", dataset_type: str, tmp_path: Path, filename: str) -> dict:
    path = tmp_path / filename
    gdf.to_file(path, driver="GeoJSON")
    with path.open("rb") as f:
        resp = client.post(
            f"/projects/{project_id}/datasets",
            data={"dataset_type": dataset_type},
            files={"file": (filename, f, "application/geo+json")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_landslide_hazard(client: TestClient, project_id: str, tmp_path: Path, *, plateau_height: float, name: str) -> dict:
    """16x16 DEM, 10m pixels, origin (0,160): flat elevation (0.0) except a
    4x4 plateau at rows 6-9/cols 6-9 raised to `plateau_height`. Horn's-
    method slope on this step produces a "ring" of high-slope cells
    surrounding the plateau (its flanks), roughly rows 5-10/cols 5-10 minus
    the flat interior (rows 7-8/cols 7-8) and the flat plateau top itself --
    verified via a standalone dry run:
    - plateau_height=3.8 -> ring is landslide class 2 ("low"), specifically
      at row 8 (y:[70,80]): cols 5,6 and 9,10 are class 2; cols 7,8
      (interior) and everywhere else is class 1.
    - plateau_height=50.0 -> same ring shape, class 5 ("very_high").
    A horizontal line at y=75 (inside row 8's y-band) from x=10 to x=150
    crosses exactly 40m of ring cells (10m each for cols 5,6,9,10).
    """
    rows, cols = 16, 16
    transform = from_origin(0, 160, 10, 10)
    elevation = np.zeros((rows, cols), dtype="float32")
    elevation[6:10, 6:10] = plateau_height
    dem_path = tmp_path / f"dem_{name}.tif"
    with rasterio.open(
        dem_path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
        crs=CRS, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(elevation, 1)
    dem = _upload_raster(client, project_id, dem_path, "dem", f"dem_{name}.tif")
    client.post(f"/datasets/{dem['id']}/derive", json={"product": "slope"})

    scenario = client.post(
        f"/projects/{project_id}/hazard-scenarios/landslide",
        json={"name": f"Routing-seed landslide {name}", "dem_dataset_id": dem["id"]},
    ).json()
    return scenario["datasets"][0]  # the landslide_susceptibility dataset


def _road_network_lines() -> "gpd.GeoDataFrame":
    """Origin (10,75) -> Destination (150,75), two options (used by tests
    that don't depend on exact route divergence, just on a working network
    + correct hazard-cost bookkeeping):
    - direct: straight line (10,75)->(150,75), length 140m. Landslide
      susceptibility classifies the WHOLE valid raster extent, not just the
      ring, so this crosses 100m of class-1 ("very_low", intensity 0.2/m)
      plus 40m of class-2 ("low", intensity 0.4/m) -- hand/tool-verified
      hazard_component_m = 100*0.2 + 40*0.4 = 36.0.
    - detour: (10,75)->(10,130)->(150,130)->(150,75), length 250m, stays
      north of the ring but still entirely within the valid (class-1)
      raster extent, so it is NOT hazard-free -- see
      test_hazard_only_routing_detours_around_moderate_hazard's dedicated
      network below for a scenario engineered to make a detour genuinely
      lower-hazard than the direct route.
    """
    direct = LineString([(10, 75), (150, 75)])
    detour_1 = LineString([(10, 75), (10, 130)])
    detour_2 = LineString([(10, 130), (150, 130)])
    detour_3 = LineString([(150, 130), (150, 75)])
    return gpd.GeoDataFrame(
        {"name": ["direct", "detour_1", "detour_2", "detour_3"]},
        geometry=[direct, detour_1, detour_2, detour_3],
        crs=CRS,
    )


def _upload_roads(client: TestClient, project_id: str, tmp_path: Path, filename: str = "roads.geojson") -> dict:
    return _upload_vector(client, project_id, _road_network_lines(), "roads", tmp_path, filename)


ORIGIN = _lonlat(10, 75)
DESTINATION = _lonlat(150, 75)


def _run_route(client: TestClient, project_id: str, **kwargs) -> "httpx.Response":  # noqa: F821
    payload = {"name": "Test Route", "origin": ORIGIN, "destination": DESTINATION, **kwargs}
    return client.post(f"/projects/{project_id}/route-analyses", json=payload)


def _run_exposure(client: TestClient, project_id: str, **kwargs) -> "httpx.Response":  # noqa: F821
    return client.post(f"/projects/{project_id}/exposure-analyses", json={"name": "Test Exposure", **kwargs})


def _run_risk(client: TestClient, project_id: str, **kwargs) -> "httpx.Response":  # noqa: F821
    return client.post(f"/projects/{project_id}/risk-analyses", json={"name": "Test Risk", **kwargs})


# --- hazard-only routing: shortest vs. hazard-aware diverge -------------------


def _detour_road_network_lines() -> "gpd.GeoDataFrame":
    """A second, purpose-built network for the divergence test below.
    Origin (12,75) -> destination (148,75):
    - direct: straight line, 136m, crosses the class-2 ring (40m of it)
      plus 96m of class-1 ("very_low") baseline -- landslide susceptibility
      classifies the ENTIRE valid raster extent (not just the ring), so
      even "very_low" ground contributes a nonzero baseline hazard cost
      (intensity 1/5 = 0.2 per meter). Hand/tool-verified total
      hazard_component_m = 96*0.2 + 40*0.4 = 35.2.
    - detour: (12,75)->(12,5)->(148,5)->(148,75), 276m, dips through the
      raster's nodata border row (y<10, unassessed -- no hazard polygon
      coverage at all) for its middle leg, and stays west/east of the ring
      (x=12/x=148 never enter the ring's x:[50,110] band) for its vertical
      legs -- entirely class-1 or unassessed. Verified total
      hazard_component_m = 65*0.2 + 65*0.2 = 26.0 (the nodata-band leg
      contributes 0).
    A longer detour is NOT automatically lower-hazard here (going around
    costs more baseline "very_low" exposure than it saves by avoiding the
    ring) -- these exact coordinates were chosen, via a standalone dry run,
    to make the detour's total hazard_component_m (26.0) genuinely lower
    than the direct route's (35.2) despite being 140m longer, so a large
    enough hazard_penalty_weight makes the hazard-aware route prefer it.
    """
    direct = LineString([(12, 75), (148, 75)])
    detour_1 = LineString([(12, 75), (12, 5)])
    detour_2 = LineString([(12, 5), (148, 5)])
    detour_3 = LineString([(148, 5), (148, 75)])
    return gpd.GeoDataFrame(
        {"name": ["direct", "detour_1", "detour_2", "detour_3"]},
        geometry=[direct, detour_1, detour_2, detour_3],
        crs=CRS,
    )


def test_hazard_only_routing_detours_around_moderate_hazard(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="mod")
    roads = _upload_vector(
        client, project_id, _detour_road_network_lines(), "roads", tmp_path, "detour_roads.geojson"
    )

    resp = _run_route(
        client, project_id,
        road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        origin=_lonlat(12, 75), destination=_lonlat(148, 75),
        hazard_penalty_weight=20.0,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    results = body["analysis"]["results"]

    assert results["feasible"] is True
    # shortest-by-distance takes the direct edge (136m); hazard-aware takes
    # the longer-but-lower-hazard detour (276m) once the penalty makes the
    # direct edge's hazard cost expensive enough -- see
    # _detour_road_network_lines' docstring for the hand/tool-verified
    # hazard totals.
    assert results["shortest_route"]["distance_m"] == pytest.approx(136.0)
    assert results["shortest_route"]["hazard_component_m"] == pytest.approx(35.2)
    assert results["hazard_aware_route"]["distance_m"] == pytest.approx(276.0)
    assert results["hazard_aware_route"]["hazard_component_m"] == pytest.approx(26.0)
    assert results["hazard_aware_route"]["hazard_component_m"] < results["shortest_route"]["hazard_component_m"]
    assert results["graph_summary"]["blocked_edge_count"] == 0  # intensity 0.4 < default block_threshold 1.0

    dataset_types = {d["dataset_type"] for d in body["datasets"]}
    assert dataset_types == {"route_shortest", "route_hazard_aware"}  # no blocked-segments layer


# --- blocking: a full-intensity class blocks the direct edge entirely -----------


def test_blocked_segment_excluded_from_both_routes(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=50.0, name="high")
    roads = _upload_roads(client, project_id, tmp_path)

    resp = _run_route(
        client, project_id,
        road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=1.0,  # irrelevant here -- blocking excludes the edge outright
    )
    assert resp.status_code == 201, resp.text
    results = resp.json()["analysis"]["results"]

    assert results["graph_summary"]["blocked_edge_count"] == 1
    assert results["graph_summary"]["total_blocked_length_m"] == pytest.approx(140.0)
    # both routes forced onto the 250m detour since the direct edge is blocked
    assert results["shortest_route"]["distance_m"] == pytest.approx(250.0)
    assert results["hazard_aware_route"]["distance_m"] == pytest.approx(250.0)

    datasets = resp.json()["datasets"]
    blocked = next(d for d in datasets if d["dataset_type"] == "route_blocked_segments")
    assert blocked["origin"] == "route_analysis"


# --- risk-aware routing: reuses Phase 7's declared weights, recomputed fresh ----


def test_risk_aware_routing_reuses_phase7_weights(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="risk")
    roads = _upload_roads(client, project_id, tmp_path)

    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=roads["id"]
    ).json()
    assert exposure["analysis"]["status"] == "completed"

    vulnerability_weight, consequence_weight = 0.5, 0.8
    risk = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=vulnerability_weight, consequence_weight=consequence_weight,
    ).json()
    assert risk["analysis"]["status"] == "completed"

    resp = _run_route(
        client, project_id,
        road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        risk_analysis_id=risk["analysis"]["id"], hazard_penalty_weight=10.0,
    )
    assert resp.status_code == 201, resp.text
    results = resp.json()["analysis"]["results"]
    assert results["hazard_source"] == "risk_analysis"

    # hand-computed: 100m of class-1 (intensity 0.2) + 40m of class-2 (intensity 0.4),
    # each risk-scored as hazard_intensity * vulnerability_weight * consequence_weight
    # -- see _road_network_lines' docstring for the hazard-only totals this builds on.
    expected_hazard_component = (100.0 * 0.2 + 40.0 * 0.4) * vulnerability_weight * consequence_weight
    assert results["shortest_route"]["hazard_component_m"] == pytest.approx(expected_hazard_component)


def test_risk_analysis_from_different_exposure_dataset_rejected(client: TestClient, tmp_path: Path) -> None:
    """The core requirement from this phase's review: vulnerability_weight/
    consequence_weight are assumptions declared for the exposure asset used
    in that RiskAnalysis and must never be silently applied to a different
    asset type (here, roads). A RiskAnalysis built from a hospitals
    exposure run must be rejected for routing, even though it references
    the exact same hazard dataset.
    """
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="mismatch")
    roads = _upload_roads(client, project_id, tmp_path)

    # Exposure analysis against a DIFFERENT asset type (hospitals, not roads).
    hospitals = gpd.GeoDataFrame({"geometry": [Point(45, 75)]}, crs=CRS)
    hospitals_ds = _upload_vector(client, project_id, hospitals, "hospitals", tmp_path, "hospitals.geojson")
    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=hospitals_ds["id"]
    ).json()
    risk = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=0.5, consequence_weight=0.5,
    ).json()
    assert risk["analysis"]["exposure_dataset_id"] == hospitals_ds["id"]

    resp = _run_route(
        client, project_id,
        road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        risk_analysis_id=risk["analysis"]["id"], hazard_penalty_weight=1.0,
    )
    assert resp.status_code == 400
    assert "exposure dataset must match the road dataset" in resp.json()["detail"].lower()


def test_risk_analysis_hazard_mismatch_rejected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide_a = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="a")
    landslide_b = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=50.0, name="b")
    roads = _upload_roads(client, project_id, tmp_path)

    exposure = _run_exposure(
        client, project_id, hazard_dataset_id=landslide_a["id"], exposure_dataset_id=roads["id"]
    ).json()
    risk = _run_risk(
        client, project_id, exposure_analysis_id=exposure["analysis"]["id"],
        vulnerability_weight=0.5, consequence_weight=0.5,
    ).json()

    resp = _run_route(
        client, project_id,
        road_dataset_id=roads["id"], hazard_dataset_id=landslide_b["id"],  # different hazard dataset
        risk_analysis_id=risk["analysis"]["id"], hazard_penalty_weight=1.0,
    )
    assert resp.status_code == 400
    assert "hazard dataset must match" in resp.json()["detail"].lower()


# --- no-feasible-route cases ---------------------------------------------------


def test_no_feasible_route_when_network_disconnected(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="disc")

    # Two entirely separate line features, far apart, never sharing an
    # endpoint -- genuinely disconnected, independent of any hazard.
    lines = gpd.GeoDataFrame(
        {"geometry": [LineString([(10, 75), (30, 75)]), LineString([(1000, 1000), (1010, 1000)])]}, crs=CRS
    )
    roads = _upload_vector(client, project_id, lines, "roads", tmp_path, "disconnected_roads.geojson")

    resp = _run_route(
        client, project_id, road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=1.0,
        origin=_lonlat(10, 75), destination=_lonlat(1005, 1000),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["analysis"]["status"] == "completed"
    assert body["analysis"]["results"]["feasible"] is False
    assert body["analysis"]["results"]["disconnected_due_to_blocking"] is False
    assert body["datasets"] == []


def test_disconnected_due_to_blocking_flag_set(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=50.0, name="block-disc")

    # ONLY the direct edge (which will be blocked) connects origin/destination.
    lines = gpd.GeoDataFrame({"geometry": [LineString([(10, 75), (150, 75)])]}, crs=CRS)
    roads = _upload_vector(client, project_id, lines, "roads", tmp_path, "single_edge_roads.geojson")

    resp = _run_route(
        client, project_id, road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=1.0,
    )
    assert resp.status_code == 201, resp.text
    results = resp.json()["analysis"]["results"]
    assert results["feasible"] is False
    assert results["disconnected_due_to_blocking"] is True


# --- validation / failure paths ------------------------------------------------


def test_origin_too_far_from_network_fails(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="far")
    roads = _upload_roads(client, project_id, tmp_path)

    resp = _run_route(
        client, project_id, road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=1.0,
        origin=_lonlat(100_000, 100_000), destination=DESTINATION,
        max_snap_distance_m=100.0,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["analysis"]["status"] == "failed"
    assert "origin" in body["analysis"]["error_message"].lower()
    assert body["datasets"] == []


def test_rejects_out_of_range_parameters(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="oob")
    roads = _upload_roads(client, project_id, tmp_path)

    resp = _run_route(
        client, project_id, road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=-1.0,
    )
    assert resp.status_code == 422

    resp = _run_route(
        client, project_id, road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=1.0, block_threshold=1.5,
    )
    assert resp.status_code == 422


def test_rejects_non_roads_dataset_type(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="badtype")
    hospitals = gpd.GeoDataFrame({"geometry": [Point(0, 0)]}, crs=CRS)
    not_roads = _upload_vector(client, project_id, hospitals, "hospitals", tmp_path, "not_roads.geojson")

    resp = _run_route(
        client, project_id, road_dataset_id=not_roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=1.0,
    )
    assert resp.status_code == 400
    assert "not 'roads'" in resp.json()["detail"]


def test_rejects_non_line_geometry_in_roads_dataset(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="badgeom")
    points_as_roads = gpd.GeoDataFrame({"geometry": [Point(10, 75), Point(20, 75)]}, crs=CRS)
    roads = _upload_vector(client, project_id, points_as_roads, "roads", tmp_path, "point_roads.geojson")

    resp = _run_route(
        client, project_id, road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=1.0,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["analysis"]["status"] == "failed"
    assert "unsupported road geometry" in body["analysis"]["error_message"].lower()


def test_missing_project_and_dataset_404s(client: TestClient) -> None:
    resp = client.post(
        "/projects/does-not-exist/route-analyses",
        json={
            "name": "x", "road_dataset_id": "a", "hazard_dataset_id": "b",
            "origin": {"lon": 0, "lat": 0}, "destination": {"lon": 0, "lat": 0},
            "hazard_penalty_weight": 1.0,
        },
    )
    assert resp.status_code == 404

    project_id = _create_project(client)
    resp = _run_route(
        client, project_id, road_dataset_id="does-not-exist", hazard_dataset_id="also-missing",
        hazard_penalty_weight=1.0,
    )
    assert resp.status_code == 404


def test_cross_project_road_dataset_rejected(client: TestClient, tmp_path: Path) -> None:
    project_a = _create_project(client)
    project_b = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_a, tmp_path, plateau_height=3.8, name="cross")
    roads = _upload_roads(client, project_b, tmp_path)

    resp = _run_route(
        client, project_a, road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=1.0,
    )
    assert resp.status_code == 404


# --- provenance / lineage / listing ------------------------------------------


def test_provenance_and_lineage_completeness(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="prov")
    roads = _upload_roads(client, project_id, tmp_path)

    resp = _run_route(
        client, project_id, road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=10.0,
    )
    body = resp.json()
    results = body["analysis"]["results"]

    assert results["method"] == "dijkstra_hazard_aware_routing"
    assert results["hazard_source"] == "hazard_only"
    assert "limitations" in results
    assert any("not a guarantee of physical safety" in lim.lower() for lim in results["limitations"])
    assert any("shared/snapped endpoint coordinates" in lim for lim in results["limitations"])

    route_datasets = client.get(f"/route-analyses/{body['analysis']['id']}/datasets").json()
    assert len(route_datasets) == 2  # shortest + hazard_aware, no blocking here
    for ds in route_datasets:
        assert ds["route_analysis_id"] == body["analysis"]["id"]
        assert ds["origin"] == "route_analysis"
        assert ds["provenance"]["route_analysis_id"] == body["analysis"]["id"]
        assert ds["provenance"]["road_dataset_id"] == roads["id"]
        assert ds["provenance"]["hazard_dataset_id"] == landslide["id"]
        assert ds["provenance"]["road_dataset_name"] == roads["name"]


def test_listing_and_detail_endpoints(client: TestClient, tmp_path: Path) -> None:
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="list")
    roads = _upload_roads(client, project_id, tmp_path)

    created = _run_route(
        client, project_id, road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        hazard_penalty_weight=1.0,
    ).json()
    analysis_id = created["analysis"]["id"]

    resp = client.get(f"/projects/{project_id}/route-analyses")
    assert [a["id"] for a in resp.json()] == [analysis_id]

    resp = client.get(f"/projects/{project_id}/route-analyses", params={"hazard_dataset_type": "flood_inundation"})
    assert resp.json() == []

    resp = client.get(f"/route-analyses/{analysis_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == analysis_id


def test_route_analysis_not_found_404s(client: TestClient) -> None:
    assert client.get("/route-analyses/does-not-exist").status_code == 404
    assert client.get("/route-analyses/does-not-exist/datasets").status_code == 404


# --- end-to-end smoke test: DEM -> hazard -> exposure -> risk -> route ---------


def test_full_pipeline_smoke_dem_to_route(client: TestClient, tmp_path: Path) -> None:
    """Exercises the entire Phase 2 -> Phase 4 -> Phase 6 -> Phase 7 ->
    Phase 8 chain in one function and walks the resulting lineage purely
    from response ids.
    """
    project_id = _create_project(client)
    landslide = _seed_landslide_hazard(client, project_id, tmp_path, plateau_height=3.8, name="smoke")
    roads = _upload_roads(client, project_id, tmp_path)

    exposure_resp = _run_exposure(
        client, project_id, hazard_dataset_id=landslide["id"], exposure_dataset_id=roads["id"]
    )
    assert exposure_resp.status_code == 201, exposure_resp.text
    exposure_body = exposure_resp.json()
    assert exposure_body["analysis"]["status"] == "completed"

    risk_resp = _run_risk(
        client, project_id, exposure_analysis_id=exposure_body["analysis"]["id"],
        vulnerability_weight=0.6, consequence_weight=0.9,
    )
    assert risk_resp.status_code == 201, risk_resp.text
    risk_body = risk_resp.json()
    assert risk_body["analysis"]["status"] == "completed"

    route_resp = _run_route(
        client, project_id,
        road_dataset_id=roads["id"], hazard_dataset_id=landslide["id"],
        risk_analysis_id=risk_body["analysis"]["id"], hazard_penalty_weight=10.0,
    )
    assert route_resp.status_code == 201, route_resp.text
    route_body = route_resp.json()
    assert route_body["analysis"]["status"] == "completed"
    assert route_body["analysis"]["results"]["feasible"] is True
    assert len(route_body["datasets"]) == 2

    # Walk the full lineage chain purely from ids returned by the API.
    route_dataset = route_body["datasets"][0]
    assert route_dataset["route_analysis_id"] == route_body["analysis"]["id"]

    route_analysis = client.get(f"/route-analyses/{route_body['analysis']['id']}").json()
    assert route_analysis["risk_analysis_id"] == risk_body["analysis"]["id"]
    assert route_analysis["road_dataset_id"] == roads["id"]
    assert route_analysis["hazard_dataset_id"] == landslide["id"]

    risk_analysis = client.get(f"/risk-analyses/{route_analysis['risk_analysis_id']}").json()
    assert risk_analysis["hazard_dataset_id"] == route_analysis["hazard_dataset_id"]
    assert risk_analysis["exposure_dataset_id"] == roads["id"]

    hazard_dataset = client.get(f"/datasets/{landslide['id']}").json()
    assert hazard_dataset["hazard_scenario_id"] is not None

    # The algorithm minimizes the COMBINED objective
    # distance_m + hazard_penalty_weight * hazard_component_m, not raw
    # hazard_component_m in isolation -- so the only property guaranteed
    # here is that the hazard-aware route's combined cost is no worse than
    # evaluating the shortest-by-distance route under that same weight
    # (Dijkstra found the minimum over the same weight function across the
    # same graph, and the shortest-by-distance path is just one candidate
    # within it). A lower raw hazard_component_m is NOT guaranteed -- see
    # ADR 0009.
    results = route_analysis["results"]
    hazard_penalty_weight = route_analysis["hazard_penalty_weight"]
    shortest_combined_cost = (
        results["shortest_route"]["distance_m"]
        + hazard_penalty_weight * results["shortest_route"]["hazard_component_m"]
    )
    assert results["hazard_aware_route"]["cost_m_equivalent"] <= shortest_combined_cost + 1e-9
