"""Missing-model API tests with a small isolated snapshot."""
from __future__ import annotations

import json
import pathlib
import sys
import zipfile
from io import BytesIO
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import assets_api  # noqa: E402


class MissingModelsTests(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.snapshot_path = pathlib.Path(self.tmp.name) / "missing_models.json"
    self.payload = {
        "schema_version": 1,
        "generated_at": "2026-11-16",
        "gaia_snapshot_date": "2026-11-05",
        "gallery_snapshot_date": "2026-11-16",
        "total": 3,
        "items": [
            {"brand": "小米", "model": "Redmi Note 13", "gaia_model_id": "m1", "brand_aliases": ["xiaomi", "redmi", "红米"]},
            {"brand": "小米", "model": "Xiaomi 14", "gaia_model_id": "m2", "brand_aliases": ["xiaomi", "redmi", "红米"]},
            {"brand": "Apple", "model": "iPhone 15 Pro", "gaia_model_id": "m3"},
        ],
    }
    self.snapshot_path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
    self.original_path = assets_api.MISSING_MODELS_PATH
    assets_api.MISSING_MODELS_PATH = self.snapshot_path

  def tearDown(self):
    assets_api.MISSING_MODELS_PATH = self.original_path
    self.tmp.cleanup()

  def test_count_search_paging_and_brand_alias(self):
    all_rows = assets_api.list_missing_models(q="", limit=2, offset=0)
    self.assertEqual(all_rows["total"], all_rows["total_all"])
    self.assertEqual(all_rows["total"], 3)
    self.assertEqual(len(all_rows["items"]), 2)
    self.assertEqual(assets_api.list_missing_models(q="Redmi", limit=50, offset=0)["items"][0]["brand"], "小米")
    self.assertEqual(assets_api.list_missing_models(q="Pro", limit=50, offset=0)["total"], 1)
    next_page = assets_api.list_missing_models(q="", limit=2, offset=2)
    self.assertEqual(next_page["items"][0]["model"], "iPhone 15 Pro")

  def test_excel_is_one_sheet_two_columns_and_matches_filter(self):
    response = assets_api.export_missing_models(q="xiaomi")
    self.assertIn("spreadsheetml.sheet", response.media_type)
    with zipfile.ZipFile(BytesIO(response.body)) as book:
      names = set(book.namelist())
      self.assertIn("xl/worksheets/sheet1.xml", names)
      self.assertFalse(any(n.startswith("xl/worksheets/sheet") and n.endswith(".xml") and n != "xl/worksheets/sheet1.xml" for n in names))
      sheet = book.read("xl/worksheets/sheet1.xml").decode()
      self.assertEqual(sheet.count("<c "), 6)
      self.assertIn("品牌", sheet)
      self.assertIn("型号", sheet)
      self.assertIn("Xiaomi 14", sheet)
      self.assertIn("Redmi Note 13", sheet)
      self.assertNotIn("iPhone 15 Pro", sheet)

  def test_bad_snapshot_count_is_not_trusted(self):
    data = json.loads(assets_api.MISSING_MODELS_PATH.read_text(encoding="utf-8"))
    data["total"] = 900
    assets_api.MISSING_MODELS_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    result = assets_api.list_missing_models(q="", limit=50, offset=0)
    self.assertEqual(result["total"], 3)


if __name__ == "__main__":
  unittest.main()
