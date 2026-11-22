"""Local-only API integration checks; all DB/session files are temporary."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix="gallery-coverage-api-")
BASE = Path(TEMP.name)
DB = BASE / "gallery.db"
POOL = BASE / "gaia_model_pool.json"
os.environ.update({"ASSET_ROOT": str(BASE), "ASSET_DB": str(DB), "THUMB_DIR": str(BASE / "thumbs"),
                   "GAIA_MODEL_POOL_PATH": str(POOL), "SESSION_DB": str(BASE / "sessions.db"),
                   "SESSION_COOKIE_SECURE": "0", "GALLERY_PASSWORD_HASH": "test-only-credential-epoch"})
POOL.write_text(json.dumps({"schema_version": 1, "generated_at": "test-snapshot",
                            "models": [{"model_id": "g1", "brand": "Apple", "model": "iPhone Test",
                                        "launch_year": 2024, "colors": ["黑色", "白色"]}]}), encoding="utf-8")
conn = sqlite3.connect(DB)
conn.execute("""create table image_assets(
    id text, category text, model_id text, standard_color text, quality_level text,
    asset_kind text, asset_type text, source_channel text, publish_state text, match_status text)""")
conn.executemany("insert into image_assets values(?,?,?,?,?,?,?,?,?,?)", [
    ("white-a", "phone", "g1", "黑色", "A", "white", "白底图", "feishu_white", None, "已匹配"),
    ("douyin", "phone", "g1", "白色", "A", "main", "抖音主图", "douyin", None, "已匹配"),
    ("low", "phone", "g1", "白色", "B", "white", "白底图", "feishu_white", None, "待处理"),
    ("unknown", "phone", "g1", "蓝色", "A", "", "未分类", "unknown", None, "待处理"),
])
conn.execute("create table douyin_gallery_items (item_id text primary key, size_bytes integer)")
conn.execute("create table douyin_gallery_folders (folder_id text primary key)")
conn.execute("insert into douyin_gallery_items values('dy-1', 123)")
conn.execute("insert into douyin_gallery_folders values('root')")
conn.commit(); conn.close()

from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import main
from app.main import app
from app import auth, assets_api, coverage_monitor
from app.coverage_service import app as coverage_app


@pytest.fixture(autouse=True)
def current_service_boundaries(monkeypatch, tmp_path):
    monkeypatch.setattr(assets_api, "ASSET_DB", DB)
    monkeypatch.setattr(main.storage_contract, "ASSET_DB", DB)
    assets_api.reset_schema_cache()
    main.storage_contract.reset_schema_cache()
    monkeypatch.setattr(coverage_monitor, "ASSET_DB", DB)
    monkeypatch.setattr(coverage_monitor, "POOL_PATH", POOL)
    monkeypatch.setattr(auth, "PASSWORD_HASH", "test-only-credential-epoch")
    monkeypatch.setattr(auth, "COOKIE_SECURE", False)
    monkeypatch.setattr(auth, "SESSION_DB", tmp_path / "sessions.db")
    auth.init_store()
    yield
    assets_api.reset_schema_cache()
    main.storage_contract.reset_schema_cache()


def test_coverage_routes_are_session_protected_and_count_only_white_a(tmp_path, monkeypatch):
    make_frontend_module(tmp_path)
    monkeypatch.setattr(main, "FRONTEND_MODULE_ROOT", tmp_path)
    anonymous = TestClient(app)
    assert anonymous.get("/api/v1/coverage-monitor").status_code == 401
    assert anonymous.get("/frontend-modules/manifest.json").status_code == 401
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, auth.create_session("127.0.0.1", "coverage-test"))
    # The gateway auth and private read service are now separate runtime units.
    private_client = TestClient(coverage_app)
    private_client.headers["x-gallery-role"] = "business"
    assert TestClient(coverage_app).get("/api/v1/coverage-monitor").status_code == 401
    response = private_client.get("/api/v1/coverage-monitor?scope=post2021")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["models_total"] == 1
    assert data["summary"]["combinations_total"] == 2
    assert data["summary"]["combinations_covered"] == 1
    assert data["summary"]["combinations_missing"] == 1
    assert data["summary"]["white_pool"]["white_A_records"] == 1
    assert data["items"][0]["color"] == "白色"
    pools = private_client.get("/api/v1/asset-pools").json()
    assert pools["overlapping_views"] is True
    counts = {p["id"]: p["asset_count"] for p in pools["pools"]}
    assert counts["white_A_visible"] == 1
    assert counts["douyin_main_separate"] == 1
    assert counts["douyin_marked_in_phone_base"] == 1
    assert client.get("/").status_code == 200
    assert 'id="module-nav-items"' in client.get("/").text
    manifest = client.get("/frontend-modules/manifest.json").json()
    assert [item["id"] for item in manifest["modules"]] == ["coverage"]
    module_script = manifest["modules"][0]["script_url"]
    assert anonymous.get(module_script).status_code == 401
    assert client.get(module_script).status_code == 200


def make_frontend_module(root, module_id="coverage", version="1.0.0", surface=None):
    version_dir = root / module_id / "versions" / version
    version_dir.mkdir(parents=True)
    metadata = {
        "id": module_id, "version": version, "title": "覆盖监控",
        "subtitle": "缺口分析", "nav_label": "覆盖监控", "icon": "◉", "order": 20,
    }
    if surface:
        metadata["surface"] = surface
    (version_dir / "module.json").write_text(json.dumps(metadata), encoding="utf-8")
    for filename in main.MODULE_FILES:
        (version_dir / filename).write_text("<div></div>", encoding="utf-8")
    (root / module_id / "current").symlink_to(f"versions/{version}")
    return version_dir


def test_frontend_module_manifest_and_asset_routes_are_scoped(tmp_path, monkeypatch):
    make_frontend_module(tmp_path)
    (tmp_path / "broken").mkdir()
    monkeypatch.setattr(main, "FRONTEND_MODULE_ROOT", tmp_path)

    manifest = main.frontend_module_manifest()
    payload = json.loads(manifest.body)
    assert [item["id"] for item in payload["modules"]] == ["coverage"]
    response = main.frontend_module_asset("coverage", "1.0.0", "module.js")
    assert str(response.path).endswith("/coverage/versions/1.0.0/module.js")
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"


def test_route_surface_module_is_not_added_to_spa_manifest_and_docs_redirects(tmp_path, monkeypatch):
    version_dir = make_frontend_module(tmp_path, module_id="docs", surface="route")
    (version_dir / "index.html").write_text(
        '<link rel="stylesheet" href="module.css"><script src="module.js"></script>',
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "FRONTEND_MODULE_ROOT", tmp_path)

    payload = json.loads(main.frontend_module_manifest().body)
    assert payload["modules"] == []
    response = main.api_guide()
    assert response.status_code == 302
    assert response.headers["location"] == "/frontend-modules/docs/1.0.0/index.html"

    anonymous = TestClient(app)
    assert anonymous.get("/docs", follow_redirects=False).status_code == 401
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, auth.create_session("127.0.0.1", "docs-module-test"))
    page = client.get("/docs", follow_redirects=False)
    assert page.status_code == 302
    module_page = client.get(page.headers["location"])
    assert module_page.status_code == 200
    assert 'href="module.css"' in module_page.text
    assert 'src="module.js"' in module_page.text


@pytest.mark.parametrize(
    "module_id,version,filename",
    [("../other", "1.0.0", "module.js"),
     ("coverage", "../latest", "module.js"),
     ("coverage", "1.0.0", "../../main.py")],
)
def test_frontend_module_asset_rejects_traversal(tmp_path, monkeypatch, module_id, version, filename):
    make_frontend_module(tmp_path)
    monkeypatch.setattr(main, "FRONTEND_MODULE_ROOT", tmp_path)
    with pytest.raises(HTTPException) as error:
        main.frontend_module_asset(module_id, version, filename)
    assert error.value.status_code == 404


def test_frontend_module_current_symlink_cannot_escape_module_root(tmp_path, monkeypatch):
    module = tmp_path / "coverage"
    module.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "module.json").write_text(json.dumps({
        "id": "coverage", "version": "1.0.0", "title": "x",
        "subtitle": "x", "nav_label": "x", "icon": "x",
    }), encoding="utf-8")
    (module / "current").symlink_to(outside)
    monkeypatch.setattr(main, "FRONTEND_MODULE_ROOT", tmp_path)
    assert main._module_current_dir("coverage") is None

