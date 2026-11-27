import io
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import coverage_monitor


def _client(monkeypatch, tmp_path):
    asset = tmp_path / "assets" / "test.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"png-bytes")
    row = {
        "id": "img-1", "model_id": 2562, "standard_color": "黑色",
        "quality_level": "A", "asset_kind": "white", "category": "phone",
        "local_path": "assets/test.png", "filename": "test.png",
        "sha256": "sha-test", "size_bytes": len(b"png-bytes"),
        "publish_state": "", "source_channel": "", "source_path": "",
    }
    model = {"brand": "Apple", "model": "iPhone 11", "model_id": 2562, "colors": ["黑色"]}
    monkeypatch.setattr(coverage_monitor, "ASSET_ROOT", tmp_path)
    monkeypatch.setattr(coverage_monitor, "_pool", lambda: {"models": [model], "generated_at": "2026-11-27T00:00:00+08:00"})
    monkeypatch.setattr(coverage_monitor, "_phone_rows", lambda: [row])
    app = FastAPI()

    @app.middleware("http")
    async def role(request, call_next):
        request.state.role = "business"
        return await call_next(request)

    app.include_router(coverage_monitor.router)
    return TestClient(app)


def test_export_count_and_zip(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    count = client.get("/api/v1/coverage-module/model-asset-download?brand=Apple&count=1")
    assert count.status_code == 200
    assert count.json()["files"] == 1
    assert count.json()["models"] == 1

    response = client.get("/api/v1/coverage-module/model-asset-download?brand=Apple")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert set(archive.namelist()) == {"manifest.csv", "Apple/iPhone 11/黑色/img-1_test.png"}
    assert "img-1" in archive.read("manifest.csv").decode("utf-8-sig")


def test_export_empty_filter(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/api/v1/coverage-module/model-asset-download?brand=NoSuchBrand&count=1")
    assert response.json()["files"] == 0
    response = client.get("/api/v1/coverage-module/model-asset-download?brand=NoSuchBrand")
    assert response.status_code == 404
