import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = ROOT / "frontend-modules/douyin-main/versions/1.0.21"


def test_douyin_main_filter_release_is_complete():
    meta = json.loads((VERSION / "module.json").read_text(encoding="utf-8"))
    assert meta["id"] == "douyin-main"
    assert meta["version"] == "1.0.21"
    assert all((VERSION / name).is_file()
               for name in ("index.html", "module.js", "module.css"))


def test_shop_filter_is_shared_in_the_top_bar():
    html = (VERSION / "index.html").read_text(encoding="utf-8")
    js = (VERSION / "module.js").read_text(encoding="utf-8")
    # 店铺筛选器放在 tab 右侧，两个页签共用。
    assert 'class="dy-topbar"' in html
    assert 'id="dyGlobalShopFilter"' in html
    assert 'id="dyGenShopFilter"' not in html
    assert "createDyFilter('dyGlobalShopFilter'" in js
    assert "onShopChange" in js
    # 主图库不再有店铺层级，从品类开始。
    assert "level === 0 ? '品类'" in js
    assert "level === 1 ? '品牌'" in js
    assert "全部店铺" not in js


def test_library_filters_reuse_the_generate_panel_dropdown():
    js = (VERSION / "module.js").read_text(encoding="utf-8")
    html = (VERSION / "index.html").read_text(encoding="utf-8")
    assert "douyin-folder-select" not in js
    assert "createDyFilter(field.querySelector('.dy-filter')" in js
    assert '<label class="dy-filter-field dy-filter-shop">' not in html
    assert '<div class="dy-filter-field dy-filter-shop">' not in html


def test_template_scope_and_category_use_the_same_dropdown():
    html = (VERSION / "index.html").read_text(encoding="utf-8")
    js = (VERSION / "module.js").read_text(encoding="utf-8")
    assert 'id="dyTemplateScopeFilter"' in html
    assert 'id="dyTemplateCategoryFilter"' in html
    assert '<select id="dyTemplateScope"' not in html
    assert '<select id="dyTemplateCategory"' not in html
    assert "当前店铺：${name}" in js
    assert "createDyFilter('dyTemplateCategoryFilter'" in js


def test_filters_close_on_outside_pointerdown_and_escape():
    js = (VERSION / "module.js").read_text(encoding="utf-8")
    assert "addEventListener('pointerdown'" in js
    assert "event.target.closest('.dy-filter')" in js
    assert "event.key === 'Escape'" in js


def test_shop_filter_shows_shop_name_without_owner():
    js = (VERSION / "module.js").read_text(encoding="utf-8")
    assert "function shopDisplayName(" in js
    assert "[（(][^（）()]*[）)]" in js
    assert "label: shopDisplayName(x.name)" in js
