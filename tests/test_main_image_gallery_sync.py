"""A generated main image must keep its gallery row metadata in sync."""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dev" / "main-image-service"))

import server  # noqa: E402


def test_sync_gallery_row_updates_canonical_row(tmp_path, monkeypatch):
    db = tmp_path / "gallery.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "create table douyin_gallery_items (item_id text primary key, local_path text, "
        "sha256 text, size_bytes integer, width integer, height integer, extension text, "
        "is_active integer default 1)")
    conn.execute(
        "insert into douyin_gallery_items values (?,?,?,?,?,?,?,?)",
        ("dy-1", "douyin_main_images/aa/old.png", "old", 100, 600, 600, "jpg", 0))
    conn.commit()
    conn.close()
    monkeypatch.setattr(server, "REAL_DB", db)
    monkeypatch.setattr(server, "GALLERY_WRITE", "1")
    target = SimpleNamespace(existing=SimpleNamespace(
        item_id="dy-1", local_path="douyin_main_images/aa/old.png"))

    assert server.sync_gallery_row(target, "newsha", 999) == ""

    row = sqlite3.connect(db).execute(
        "select sha256,size_bytes,width,height,extension,is_active from douyin_gallery_items "
        "where item_id='dy-1'").fetchone()
    assert row == ("newsha", 999, 1080, 1080, "png", 1)
