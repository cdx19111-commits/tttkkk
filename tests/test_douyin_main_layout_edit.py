"""Layout save updates the current version in place; delete affects one version only."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = ROOT / "frontend-modules/douyin-main/versions/1.0.21"


def test_layout_save_is_in_place_and_delete_is_version_scoped():
    html = (VERSION / "index.html").read_text(encoding="utf-8")
    js = (VERSION / "module.js").read_text(encoding="utf-8")

    assert "保存版式" in html
    assert "保存为新版本" not in html
    assert "版式已保存（版本 " in js
    assert "其他版本不会受影响" in js
    assert 'data-dy-template-version="' in js


def test_editor_uses_the_server_font_and_matches_text_centering():
    js = (VERSION / "module.js").read_text(encoding="utf-8")
    css = (VERSION / "module.css").read_text(encoding="utf-8")

    assert "@font-face" in css
    assert "MainImageTemplate" in css
    assert "/api/v1/main-images/font" in css
    assert "document.fonts.load('400 20px MainImageTemplate')" in js
    assert "MainImageTemplate" in js
    assert "measureText" in js
    assert "webkitTextStrokeWidth" in js
    text_rule = css.split(".dy-layout-text {", 1)[1].split("}", 1)[0]
    assert "font-family:'MainImageTemplate'" in text_rule
    assert "text-shadow" not in text_rule
