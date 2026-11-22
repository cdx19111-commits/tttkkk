import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_unified_platform_module_is_complete_and_versioned():
    version = ROOT / "frontend-modules/asset-platform/versions/1.0.3"
    meta = json.loads((version / "module.json").read_text(encoding="utf-8"))
    assert meta["id"] == "asset-platform"
    assert meta["title"] == meta["nav_label"] == "图片资产管理平台"
    assert all((version / name).is_file() for name in ("index.html", "module.js", "module.css"))
    html = (version / "index.html").read_text(encoding="utf-8")
    js = (version / "module.js").read_text(encoding="utf-8")
    assert all(label in html for label in ("A 池", "B 池", "C 池", "apSuggestions"))
    assert "/frontend-modules/white-gallery/1.0.3/index.html" in js
    assert "/frontend-modules/white-gallery/1.0.3/module.js" in js
    css = (version / "module.css").read_text(encoding="utf-8")
    assert "/frontend-modules/white-gallery/1.0.3/module.css" in css
    assert "data-view=\"library\"" in js
    assert "data-view=\"white-gallery\"" in js
    # B/C keep their existing keyword search contract; no new facet filters are added.
    assert "request(`/${platform.pool}/items?${params.toString()}`)" in js
    assert "params.set('brand'" not in js and "params.set('color'" not in js
    assert all(path in js for path in (
        "/api/v1/images-facets", "/api/v1/brand-tree", "/api/v1/pool-management/",
        "/assets/${encodeURIComponent(platform.current.asset_id)}/pool",
    ))
    assert "const $ = (id, root) => root.querySelector" in js
    assert "platform.poolCounts?.A" in js
    assert "#decisions" in js and "#white-gallery" in js


def test_api_guide_lists_pool_api_from_live_openapi():
    version = ROOT / "frontend-modules/docs/versions/1.0.3"
    meta = json.loads((version / "module.json").read_text(encoding="utf-8"))
    html = (version / "index.html").read_text(encoding="utf-8")
    js = (version / "module.js").read_text(encoding="utf-8")
    assert meta["version"] == "1.0.3" and meta["surface"] == "route"
    assert 'id="apiInventory"' in html and "/openapi.json" in js
    assert "/api/v1/pool-management/{pool}/items" in js
    assert "当前接口清单" in html and "Swagger 接口详情" in html
    assert "model_exact" in html and "model_source" in html
    assert "asset_kind" in html and "asset_type" in html and "white" in html
    assert '"/api/v1/coverage/{model_id}": "读取单个 Gaia 型号的标准颜色覆盖情况"' in js


def test_current_platform_resolves_white_gallery_release_from_manifest():
    version = ROOT / "frontend-modules/asset-platform/versions/1.0.4"
    js = (version / "module.js").read_text()
    css = (version / "module.css").read_text()
    assert "/frontend-modules/manifest.json" in js
    assert "galleryModule.html_url" in js
    assert "galleryModule.script_url" in js
    assert "galleryModule.style_url" in js
    assert "/white-gallery/1.0.3/" not in js + css
