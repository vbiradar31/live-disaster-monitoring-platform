import asyncio

import httpx

from app.database import get_db
from app.main import app


class FakeDB:
    def execute(self, statement):
        return None


async def request(method, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path)


def test_health_endpoint_checks_database_dependency():
    app.dependency_overrides[get_db] = lambda: FakeDB()
    try:
        response = asyncio.run(request("GET", "/health"))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "database": "connected"}


def test_one_shot_ingestion_endpoint_returns_per_provider_results(monkeypatch):
    class FakeService:
        def __init__(self, db):
            pass

        def run_all(self):
            return {"providers": [{"provider": "gdacs", "status": "failed"}, {"provider": "usgs", "status": "completed"}]}

        def close(self):
            pass

    monkeypatch.setattr("app.main.EarthquakeIngestionService", FakeService)
    app.dependency_overrides[get_db] = lambda: FakeDB()
    try:
        response = asyncio.run(request("POST", "/ingestion/earthquakes/run"))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [item["status"] for item in response.json()["providers"]] == ["failed", "completed"]
