import io
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import business_review, coverage_monitor


def _png(size=(800, 800)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _coverage_row(model_id="42", color="薰衣草紫"):
    return {"id": "existing", "category": "phone", "model_id": model_id,
            "standard_color": color, "quality_level": "A", "asset_kind": "white",
            "asset_type": "white"}


def _app(monkeypatch, tmp_path):
    monkeypatch.setattr(coverage_monitor, "_pool", lambda: {"models": [{
        "model_id": "42", "brand": "Apple", "model": "iPhone 17", "launch_year": 2025,
        "colors": ["薰衣草紫", "黑色"]}]})
    monkeypatch.setattr(coverage_monitor, "_phone_rows", lambda: [])
    monkeypatch.setattr(coverage_monitor, "model_params_for", lambda _mid: [
        {"name": "存储容量", "value": "256GB"}])
    monkeypatch.setattr(business_review, "ASSET_ROOT", tmp_path)
    monkeypatch.setattr(business_review, "ASSET_DIR", tmp_path / "business_review_assets")
    monkeypatch.setattr(business_review, "ASSET_DB", tmp_path / "gallery.db")
    business_review.init_schema()

    async def context(_request, model_id, color):
        return {"model_id": model_id, "brand": "Apple", "model": "iPhone 17",
                "color": color, "can_upload": True,
                "model_params": [{"name": "存储容量", "value": "256GB"}]}

    monkeypatch.setattr(business_review, "_coverage_upload_context", context)
    app = FastAPI()

    @app.middleware("http")
    async def role(request, call_next):
        request.state.role = "business"
        return await call_next(request)

    app.include_router(coverage_monitor.router)
    app.include_router(business_review.router)
    return app


def test_context_uses_exact_gaia_id_color_and_params(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(coverage_monitor, "_phone_rows", lambda: [])
    response = TestClient(app).get("/api/v1/coverage-upload-context",
                                  params={"model_id": "42", "color": "薰衣草紫"})
    assert response.status_code == 200
    data = response.json()
    assert data["model_id"] == "42"
    assert data["color"] == "薰衣草紫"
    assert data["model_params"] == [{"name": "存储容量", "value": "256GB"}]
    assert data["can_upload"] is True


def test_context_rejects_wrong_color_and_currently_covered_combo(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    client = TestClient(app)
    wrong = client.get("/api/v1/coverage-upload-context", params={"model_id": "42", "color": "紫色"})
    assert wrong.status_code == 422
    monkeypatch.setattr(coverage_monitor, "_phone_rows", lambda: [_coverage_row()])
    monkeypatch.setattr(business_review, "read_phone_rows", lambda _path: [_coverage_row()])
    covered = client.get("/api/v1/coverage-upload-context", params={"model_id": "42", "color": "薰衣草紫"})
    assert covered.status_code == 409


def test_review_guard_prevents_second_a_image_for_same_combo(monkeypatch, tmp_path):
    _app(monkeypatch, tmp_path)
    monkeypatch.setattr(coverage_monitor, "_phone_rows", lambda: [_coverage_row()])
    monkeypatch.setattr(business_review, "read_phone_rows", lambda _path: [_coverage_row()])
    try:
        business_review._ensure_combo_uncovered("42", "薰衣草紫")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("covered model/color combination must reject a second A image")


def test_upload_is_queued_pending_and_idempotent_without_gallery_write(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    client = TestClient(app)
    content = _png()
    params = {"model_id": "42", "color": "薰衣草紫", "filename": "iphone17.png"}
    first = client.post("/api/v1/business-review/uploads", params=params,
                        content=content, headers={"content-type": "image/png"})
    assert first.status_code == 201, first.text
    item = first.json()
    assert item["status"] == "pending"
    assert "审核通过后才计入 A 级覆盖" in item["message"]
    duplicate = client.post("/api/v1/business-review/uploads", params=params,
                            content=content, headers={"content-type": "image/png"})
    assert duplicate.status_code == 201
    assert duplicate.json()["duplicate"] is True
    with sqlite3.connect(tmp_path / "gallery.db") as conn:
        row = conn.execute("select status,gaia_model_id,allowed_colors_json,source_evidence_json from business_review_items").fetchone()
        tables = {x[0] for x in conn.execute("select name from sqlite_master where type='table'")}
    assert row[0:3] == ("pending", "42", '["薰衣草紫"]')
    assert json.loads(row[3])["model_params"] == [{"name": "存储容量", "value": "256GB"}]
    assert "image_assets" not in tables
    assert len(list((tmp_path / "business_review_assets").glob("*.png"))) == 1


def test_upload_rejects_corrupt_or_small_images(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    client = TestClient(app)
    params = {"model_id": "42", "color": "薰衣草紫"}
    small = client.post("/api/v1/business-review/uploads", params=params,
                        content=_png((500, 800)), headers={"content-type": "image/png"})
    assert small.status_code == 422
    corrupt = client.post("/api/v1/business-review/uploads", params=params,
                          content=b"not an image", headers={"content-type": "image/png"})
    assert corrupt.status_code == 422
    wrong_mime = client.post("/api/v1/business-review/uploads", params=params,
                             content=_png(), headers={"content-type": "image/jpeg"})
    assert wrong_mime.status_code == 415
