"""The Douyin gallery must hide archived rows and still serve active files."""
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import douyin_gallery


def test_active_filter_and_image_serving(tmp_path, monkeypatch):
    db = tmp_path / "gallery.db"
    root = tmp_path / "v03"
    asset = root / "douyin_main_images" / "aa" / "active.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"active-png")
    archived = root / "douyin_main_images" / "bb" / "archived.png"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"archived-png")

    monkeypatch.setattr(douyin_gallery, "ASSET_DB", db)
    monkeypatch.setattr(douyin_gallery, "ASSET_ROOT", root)
    douyin_gallery.init_schema()

    conn = sqlite3.connect(db)
    conn.execute("""insert into douyin_gallery_folders
        (folder_id,parent_id,name,path,source_token) values (?,?,?,?,?)""",
        (douyin_gallery.ROOT_ID, "", "抖音主图", "", douyin_gallery.ROOT_TOKEN))
    conn.executemany("""insert into douyin_gallery_items
        (item_id,source_token,folder_id,source_path,name,extension,local_path,sha256,
         size_bytes,width,height,source_url,imported_at,is_active)
        values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
        ("dy-active", "tok-active", douyin_gallery.ROOT_ID,
         "抖音主图/129675602/手机/苹果/iPhone 11/active.png", "active.png", "png",
         "douyin_main_images/aa/active.png", "sha-active", 10, 1080, 1080, "", "now", 1),
        ("dy-archived", "tok-archived", douyin_gallery.ROOT_ID,
         "抖音主图/129675602/手机/苹果/iPhone 11/archived.png", "archived.png", "png",
         "douyin_main_images/bb/archived.png", "sha-archived", 12, 1080, 1080, "", "now", 0),
    ])
    conn.commit()
    conn.close()

    app = FastAPI()

    @app.middleware("http")
    async def login(request, call_next):
        request.state.role = "business"
        return await call_next(request)

    app.include_router(douyin_gallery.router)
    client = TestClient(app)

    summary = client.get("/api/v1/douyin-gallery/summary").json()
    assert summary["images"] == 1
    assert summary["size_bytes"] == 10

    listed = client.get("/api/v1/douyin-gallery/items?limit=50").json()
    assert listed["total"] == 1
    assert [item["item_id"] for item in listed["items"]] == ["dy-active"]

    assert client.get("/api/v1/douyin-gallery/items/dy-active/image").status_code == 200
    assert client.get("/api/v1/douyin-gallery/items/dy-archived/image").status_code == 404
