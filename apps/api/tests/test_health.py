from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "crisis-x-api"


def test_root_returns_message() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()
