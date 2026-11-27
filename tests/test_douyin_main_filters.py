import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = ROOT / "frontend-modules/douyin-main/versions/1.0.17"


def test_douyin_main_filter_release_is_complete():
    meta = json.loads((VERSION / "module.json").read_text(encoding="utf-8"))
    assert meta["id"] == "douyin-main"
    assert meta["version"] == "1.0.17"
    assert all((VERSION / name).is_file()
               for name in ("index.html", "module.js", "module.css"))


def test_library_filters_reuse_the_generate_panel_dropdown():
    js = (VERSION / "module.js").read_text(encoding="utf-8")
    html = (VERSION / "index.html").read_text(encoding="utf-8")
    # 主图库目录筛选不再使用原生 select，而是复用主图生成的 dy-filter 下拉组件。
    assert "douyin-folder-select" not in js
    assert "level === 0 ? '店铺'" in js
    assert "createDyFilter(field.querySelector('.dy-filter')" in js
    # 下拉字段外层改用 div，避免 label 的默认激活行为在点击后重新打开菜单。
    assert '<label class="dy-filter-field dy-filter-shop">' not in html
    assert '<div class="dy-filter-field dy-filter-shop">' in html


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
    assert "label: level === 0 ? shopDisplayName(folder.name) : folder.name" in js
