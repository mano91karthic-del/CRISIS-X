from fastapi.testclient import TestClient


def test_create_and_list_project(client: TestClient) -> None:
    resp = client.post("/projects", json={"name": "Test Project", "description": "desc"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test Project"
    assert body["description"] == "desc"
    assert "id" in body

    resp = client.get("/projects")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_create_project_without_description(client: TestClient) -> None:
    resp = client.post("/projects", json={"name": "No Description"})
    assert resp.status_code == 201
    assert resp.json()["description"] is None


def test_get_project_not_found(client: TestClient) -> None:
    resp = client.get("/projects/does-not-exist")
    assert resp.status_code == 404


def test_get_project_by_id(client: TestClient) -> None:
    created = client.post("/projects", json={"name": "Lookup Me"}).json()
    resp = client.get(f"/projects/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]
