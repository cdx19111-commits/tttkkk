"""A 级即业务可用：quality_flags 不再参与准入判定。

业务方 2026-11-26 确认：手机白底图库中 quality_level=A 的图片即业务可用，
历史批次的 quality_flags 格式差异不应导致调用方被拦截。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import listing_api, storage_contract as assets_api


@pytest.fixture
def asset_root(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    monkeypatch.setattr(assets_api, "ASSET_ROOT", tmp_path)
    return tmp_path


def _row(**overrides):
    row = {
        "id": "img_test",
        "format": "png",
        "width": 800,
        "height": 800,
        "size_bytes": 50_000,
        "local_path": "assets/a.png",
        "quality_flags": None,
        "verify_state": None,
    }
    row.update(overrides)
    return row


def test_a_level_without_flags_is_eligible(asset_root):
    """历史 A 级但 quality_flags 为空 → 放行。"""
    result = listing_api._evaluate_white(_row())
    assert result["status"] == "eligible"
    assert result["reasons"] == []


def test_legacy_flags_schema_does_not_block(asset_root):
    """旧批次用 promotional_text 而不是 marketing_or_watermark → 不再拦截。"""
    legacy = {"promotional_text": "no", "device_complete": True, "white_background": "yes"}
    result = listing_api._evaluate_white(_row(quality_flags=json.dumps(legacy)))
    assert result["status"] == "eligible"


def test_explicitly_bad_flags_do_not_block(asset_root):
    """即使历史标记写了不合格，也按 A 级业务口径放行。"""
    bad = {"white_background": "no", "single_product": False, "marketing_or_watermark": "yes"}
    result = listing_api._evaluate_white(_row(quality_flags=json.dumps(bad)))
    assert result["status"] == "eligible"


def test_non_square_is_ineligible(asset_root):
    result = listing_api._evaluate_white(_row(width=1067, height=800))
    assert result["status"] == "ineligible"
    assert any(r["code"] == "not_square" for r in result["reasons"])


def test_too_small_is_ineligible(asset_root):
    result = listing_api._evaluate_white(_row(width=480, height=480))
    assert result["status"] == "ineligible"
    assert any(r["code"] == "too_small" for r in result["reasons"])


def test_too_large_is_ineligible(asset_root):
    result = listing_api._evaluate_white(_row(size_bytes=3_000_001))
    assert result["status"] == "ineligible"
    assert any(r["code"] == "too_large" for r in result["reasons"])


def test_unsupported_format_is_ineligible(asset_root):
    result = listing_api._evaluate_white(_row(format="gif"))
    assert result["status"] == "ineligible"
    assert any(r["code"] == "unsupported_format" for r in result["reasons"])


def test_output_flags_match_listing_contract(asset_root):
    """对外必须是 5 键、类型稳定，且 single_product 是布尔。"""
    flags = listing_api._evaluate_white(_row())["flags"]
    assert flags == {
        "white_background": "yes",
        "single_product": True,
        "marketing_or_watermark": "no",
        "model_mismatch_suspicion": "none",
        "human_review": "confirmed",
    }
    assert isinstance(flags["single_product"], bool)


def test_review_basis_distinguishes_human_approval(asset_root):
    human = listing_api._evaluate_white(_row(verify_state="human_approved"))
    batch = listing_api._evaluate_white(_row())
    assert human["review_basis"] == "per_image_human_approved"
    assert batch["review_basis"] == "business_batch_confirmed_20261126"


def test_main_object_is_listable():
    """主图不再返回 unknown/null，避免调用方判为不可用。"""
    row = {
        "item_id": "main_1",
        "name": "x.jpg",
        "source_path": "抖音主图/shop/x.jpg",
        "extension": "jpg",
        "width": 800,
        "height": 800,
        "size_bytes": 1_000,
        "sha256": "a" * 64,
        "imported_at": "",
    }
    model = {"brand_id": "1", "brand": "小米", "model_id": "8144", "model": "小米 MIX Fold 3"}
    obj = listing_api._main_object(row, model)
    assert obj["douyin_eligible"] is True
    assert obj["eligibility_status"] == "eligible"
    assert obj["quality_flags"]["marketing_or_watermark"] == "no"
    assert obj["reject_reasons"] == []


def test_brand_key_splits_parenthesized_english_name():
    """Gaia 品牌写成「华为（HUAWEI）」时也要能用「华为」命中。"""
    model = {"brand": "华为（HUAWEI）", "gaia_brand": "华为（HUAWEI）"}
    keys = listing_api._model_brand_keys(model)
    assert "huawei" in keys


def test_brand_aliases_include_name_outside_parentheses():
    """不在品牌字典里的品牌（如华硕），也要能用括号外主名剥型号前缀。"""
    model = {"brand": "华硕（ASUS）", "gaia_brand": "华硕（ASUS）"}
    aliases = listing_api._brand_aliases_for(model)
    assert "华硕" in aliases


def test_model_aliases_strip_parenthesized_brand_prefix():
    model = {"brand": "华为（HUAWEI）", "model": "华为 畅享 50 pro"}
    assert "畅享50pro" in listing_api._model_aliases(model)


def _write_pool(tmp_path, monkeypatch, models):
    for entry in models:
        entry.setdefault("category", "手机")
    path = tmp_path / "gaia_pool.json"
    path.write_text(json.dumps({"models": models}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(listing_api, "GAIA_POOL_PATH", path)


def test_resolve_model_by_plain_chinese_brand(tmp_path, monkeypatch):
    """调用方传「华为」「荣耀」这类主品牌名必须能查到型号。"""
    _write_pool(tmp_path, monkeypatch, [
        {"model_id": "13086", "brand": "华为（HUAWEI）", "gaia_brand": "华为（HUAWEI）",
         "model": "华为 畅享 50 pro", "colors": ["雪域白"]},
    ])
    model, level, _ = listing_api._resolve_model("", "华为", "畅享 50 pro")
    assert level == "exact"
    assert model["model_id"] == "13086"


def test_duplicate_gaia_records_return_candidates_not_guess(tmp_path, monkeypatch):
    """Gaia 池里同一款机有重复记录时，返回候选而不是猜一条。"""
    _write_pool(tmp_path, monkeypatch, [
        {"model_id": "969", "brand": "华为（HUAWEI）", "model": "华为 nova 2s", "colors": []},
        {"model_id": "3556", "brand": "华为（HUAWEI）", "model": "华为 nova2s", "colors": []},
    ])
    model, level, candidates = listing_api._resolve_model("", "华为", "nova 2s")
    assert model is None
    assert level == "ambiguous"
    assert {c["model_id"] for c in candidates} == {"969", "3556"}
