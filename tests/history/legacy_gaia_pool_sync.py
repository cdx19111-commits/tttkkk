import importlib.util
import json
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
spec = importlib.util.spec_from_file_location("build_model_pool", PROJECT_ROOT / "tools/gaia/build_model_pool.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def test_exact_id_aggregation_and_retail_brand_display():
    rows = [
        {"model_id": "1", "model_name": "Redmi Note 13 Pro+", "brand_id": "x", "brand_name": "小米",
         "category_name": "手机", "standard_sku_id": "s1", "color": "黑色"},
        {"model_id": "1", "model_name": "Redmi Note 13 Pro+", "brand_id": "x", "brand_name": "小米",
         "category_name": "手机", "standard_sku_id": "s2", "color": "黑色"},
    ]
    pool = sync.build_from_rows(rows, {"1": {"launch_time": "2023年10月"}}, source="test")
    item = pool["models"][0]
    assert pool["model_count"] == 1 and pool["model_color_pairs"] == 1
    assert item["sku_count"] == 2 and item["record_count"] == 2
    assert item["brand"] == "Redmi" and item["gaia_brand"] == "小米"
    assert item["launch_year"] == 2023
    assert item["model"].endswith("Pro+")


def test_incomplete_refresh_does_not_replace_previous_pool():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "pool.json"
        original = {"schema_version": 1, "model_count": 100, "model_color_pairs": 500,
                    "models": [{"model_id": str(i)} for i in range(100)]}
        path.write_text(json.dumps(original), encoding="utf-8")
        incomplete = {"schema_version": 1, "model_count": 10, "model_color_pairs": 20,
                      "models": [{"model_id": str(i)} for i in range(10)]}
        try:
            sync.write_atomic(incomplete, path)
        except RuntimeError:
            pass
        else:
            raise AssertionError("incomplete refresh unexpectedly replaced the previous pool")
        assert json.loads(path.read_text(encoding="utf-8"))["model_count"] == 100

