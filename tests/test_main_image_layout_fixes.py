"""Layout text must be centered by its ink box, and template edits must be in-place."""
import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dev" / "main-image-service"))
sys.path.insert(0, str(ROOT / "dev" / "main-image-service" / "tool"))

import server  # noqa: E402
from sample_config import FONT_PATH  # noqa: E402
from utils import draw_text_with_max_width, load_font  # noqa: E402


def _ink_bbox(image):
    return image.getchannel("A").getbbox()


def test_text_is_centered_by_ink_box_not_font_ascent():
    if not Path(FONT_PATH).is_file():
        pytest.skip("本机没有模板字体")
    image = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = load_font(FONT_PATH, 80)
    draw_text_with_max_width(draw, "苹果", (540, 300), font, "#000000",
                             max_width=600, align="center", origin="center")
    box = _ink_bbox(image)
    assert box is not None
    center_x = (box[0] + box[2]) / 2
    center_y = (box[1] + box[3]) / 2
    assert abs(center_x - 540) <= 3
    assert abs(center_y - 300) <= 3


def test_text_topleft_uses_ink_box():
    if not Path(FONT_PATH).is_file():
        pytest.skip("本机没有模板字体")
    image = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = load_font(FONT_PATH, 60)
    draw_text_with_max_width(draw, "苹果", (100, 100), font, "#000000",
                             max_width=600, align="left", origin="topleft")
    box = _ink_bbox(image)
    assert box is not None
    assert abs(box[0] - 100) <= 3
    assert abs(box[1] - 100) <= 3


def test_text_still_shrinks_to_max_width():
    if not Path(FONT_PATH).is_file():
        pytest.skip("本机没有模板字体")
    image = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = load_font(FONT_PATH, 80)
    width, _height = draw_text_with_max_width(
        draw, "苹果手机超长文字", (540, 300), font, "#000000",
        max_width=120, align="center", origin="center")
    assert width <= 122


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture()
def template_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    template_dir = tmp_path / "templates"
    data_dir.mkdir()
    template_dir.mkdir()
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "TEMPLATE_DIR", template_dir)
    monkeypatch.setattr(server, "SERVICE_DB", data_dir / "main_images.db")
    server.init_db()
    with sqlite3.connect(server.SERVICE_DB) as conn:
        conn.execute("delete from main_templates")
        conn.commit()
    return template_dir


def _insert_template(conn, image_path, version, active, config=None):
    config = config or {
        "product_image": {"center_x": 540, "center_y": 540, "max_width": 520, "max_height": 650},
        "product_name": {"center_x": 540, "center_y": 150, "font_size": 52,
                         "color": "#1a1a1a", "max_width": 520, "align": "center",
                         "bold": True, "text": ""},
    }
    cur = conn.execute(
        """insert into main_templates
           (scope, shop_id, category, name, image_path, config_json, version, active, created_at)
           values ('shop','129675602','ios_phone','苹果手机',?,?,?,?, '2026-11-27T00:00:00')""",
        (image_path, json.dumps(config, ensure_ascii=False), version, active),
    )
    return cur.lastrowid


def test_layout_save_updates_the_same_version_in_place(template_db):
    image = template_db / "shared.png"
    image.write_bytes(b"image")
    with closing(sqlite3.connect(server.SERVICE_DB)) as conn:
        conn.row_factory = sqlite3.Row
        first = _insert_template(conn, str(image), 1, 0)
        second = _insert_template(conn, str(image), 2, 1)
        conn.commit()

    result = asyncio.run(server.update_template_config(
        second, FakeRequest({"product_name": {"center_y": 220, "text": "新文字"}})))

    assert result["id"] == second
    assert result["version"] == 2
    assert result["config"]["product_name"]["center_y"] == 220
    assert result["config"]["product_name"]["text"] == "新文字"
    with closing(sqlite3.connect(server.SERVICE_DB)) as conn:
        rows = conn.execute("select id,version,active from main_templates order by id").fetchall()
    assert rows == [(first, 1, 0), (second, 2, 1)]
    assert image.read_bytes() == b"image"


def test_delete_one_version_keeps_the_other_and_promotes_next(template_db):
    shared = template_db / "shared.png"
    shared.write_bytes(b"shared")
    with closing(sqlite3.connect(server.SERVICE_DB)) as conn:
        first = _insert_template(conn, str(shared), 1, 0)
        second = _insert_template(conn, str(shared), 2, 1)
        conn.commit()

    result = server.delete_template(second)

    assert result["deleted_version_count"] == 1
    assert result["next_active"] == first
    with closing(sqlite3.connect(server.SERVICE_DB)) as conn:
        rows = conn.execute("select id,active from main_templates").fetchall()
    assert rows == [(first, 1)]
    assert shared.read_bytes() == b"shared"


def test_delete_last_reference_removes_the_file(template_db):
    image = template_db / "only.png"
    image.write_bytes(b"only")
    with closing(sqlite3.connect(server.SERVICE_DB)) as conn:
        only = _insert_template(conn, str(image), 1, 1)
        conn.commit()

    server.delete_template(only)

    assert not image.exists()
    with closing(sqlite3.connect(server.SERVICE_DB)) as conn:
        assert conn.execute("select count(*) from main_templates").fetchone()[0] == 0


def test_move_category_only_moves_the_selected_version(template_db):
    image = template_db / "shared.png"
    image.write_bytes(b"shared")
    with closing(sqlite3.connect(server.SERVICE_DB)) as conn:
        first = _insert_template(conn, str(image), 1, 0)
        second = _insert_template(conn, str(image), 2, 1)
        conn.commit()

    asyncio.run(server.move_template_category(second, FakeRequest({"category": "android_phone"})))

    with closing(sqlite3.connect(server.SERVICE_DB)) as conn:
        rows = conn.execute("select id,category,active from main_templates order by id").fetchall()
    assert rows == [(first, "ios_phone", 1), (second, "android_phone", 1)]


def test_font_endpoint_serves_the_configured_template_font():
    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "def template_font(" in source
    assert '"/api/v1/main-images/font"' in source
    assert "font/collection" in source
    assert "private, max-age=86400" in source
