import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.coverage_engine import asset_pools_summary, white_pool_rows, calculate


def test_coverage_requires_explicit_white_a_and_exact_model_color():
    inputs = [
        dict(model_id="1", standard_color="薰衣草紫", quality_level="A", asset_kind="white", asset_type="白底图", source_channel="feishu_white", id="a1"),
        dict(model_id="1", standard_color="薰衣草紫", quality_level="A", asset_kind="white", asset_type="白底图", source_channel="feishu_white", id="a2"),
        dict(model_id="2", standard_color="黑色", quality_level="A", asset_kind="main", asset_type="抖音主图", source_channel="douyin", id="d1"),
        dict(model_id="3", standard_color="白色", quality_level="B", asset_kind="white", asset_type="白底图", source_channel="feishu_white", id="b1"),
        dict(model_id="4", standard_color="绿色", quality_level="A", asset_kind="", asset_type="未知图片", source_channel="unknown", id="u1"),
    ]
    rows, excluded = white_pool_rows(inputs)
    assert len(rows) == 2
    assert excluded["not_A"] == 1
    assert excluded["purpose_unknown_or_conflict"] == 2
    pool = {"generated_at": "test", "models": [
        {"model_id": "1", "brand": "Apple", "model": "iPhone 17", "colors": ["薰衣草紫", "白色"], "launch_year": 2025},
        {"model_id": "2", "brand": "OPPO", "model": "A", "colors": ["黑色"], "launch_year": 2024},
    ]}
    result = calculate(pool, rows, excluded)
    assert result["summary"]["combinations_total"] == 3
    assert result["summary"]["combinations_covered"] == 1
    assert result["summary"]["duplicate_A_combinations"] == 1
    assert result["summary"]["models_no_A"] == 1
    assert len(result["items"]) == 2
    pools = asset_pools_summary(inputs)
    pool_counts = {x["id"]: x["asset_count"] for x in pools["pools"]}
    assert pools["overlapping_views"] is True
    assert pool_counts["white_A_visible"] == 2
    assert pool_counts["douyin_marked_in_phone_base"] == 1
    assert pool_counts["review_queue"] >= 2


def test_post_2021_scope_does_not_guess_unknown_launch_year():
    pool = {"generated_at": "test", "models": [
        {"model_id": "1", "brand": "A", "model": "known", "colors": ["black"], "launch_year": 2021},
        {"model_id": "2", "brand": "B", "model": "unknown", "colors": ["white"], "launch_year": None},
    ]}
    result = calculate(pool, [], {}, scope="post2021")
    assert result["summary"]["models_total"] == 1
    assert result["summary"]["models_no_A"] == 1
    assert result["summary"]["launch_year_unknown"] == 0

