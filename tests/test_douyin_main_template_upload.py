"""Frontend contract for 800–3000 square template uploads."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = ROOT / "frontend-modules/douyin-main/versions/1.0.20"


def test_template_upload_accepts_square_sizes_and_normalizes_server_side():
    html = (VERSION / "index.html").read_text(encoding="utf-8")
    js = (VERSION / "module.js").read_text(encoding="utf-8")

    assert 'accept="image/png,image/jpeg,image/webp"' in html
    assert "800–3000" in html
    assert "自动转为 1080×1080" in html
    assert "800–3000" in js
    assert "模板必须是正方形" in js
    assert "模板边长需在 800–3000 之间" in js
    assert "仅支持 PNG、JPEG、WebP 模板" in js
