import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = ROOT / "frontend-modules/douyin-main/versions/1.0.21"


def test_download_release_is_complete():
    meta = json.loads((VERSION / "module.json").read_text(encoding="utf-8"))
    assert meta["id"] == "douyin-main"
    assert meta["version"] == "1.0.21"
    assert all((VERSION / name).is_file()
               for name in ("index.html", "module.js", "module.css"))


def test_library_cards_are_selectable_and_have_a_batch_bar():
    html = (VERSION / "index.html").read_text(encoding="utf-8")
    js = (VERSION / "module.js").read_text(encoding="utf-8")
    assert 'id="douyinSelectionBar"' in html
    assert 'id="douyinSelectAll"' in html
    assert 'id="douyinClearSelection"' in html
    assert 'id="douyinDownloadSelected"' in html
    assert "data-douyin-select" in js
    assert "function toggleDouyinSelect(" in js
    assert "function selectAllLoadedDouyin(" in js
    assert "function clearDouyinSelection(" in js


def test_batch_download_builds_a_zip_client_side():
    js = (VERSION / "module.js").read_text(encoding="utf-8")
    assert "const DOUYIN_MAX_BATCH = 100;" in js
    assert "const DOUYIN_MAX_BYTES = 300 * 1024 * 1024;" in js
    assert "async function downloadSelectedDouyin(" in js
    assert "function douyinZipStore(" in js
    assert "function douyinCrc32(" in js
    assert "0x04034b50" in js and "0x02014b50" in js and "0x06054b50" in js
    assert "URL.createObjectURL" in js
    assert ".zip" in js
    assert "credentials: 'same-origin'" in js
