"""Dependency-free coverage calculations shared by API and offline checks."""
from __future__ import annotations

import re
from datetime import datetime

DOUYIN_MARKERS = ("抖音", "douyin", "tiktok")
MAIN_IMAGE_MARKERS = ("主图", "marketing", "feed_image")


def _norm(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def white_pool_rows(rows: list[dict]) -> tuple[list[dict], dict]:
    accepted, excluded = [], {"not_A": 0, "purpose_unknown_or_conflict": 0,
                              "missing_model_or_color": 0, "hidden_or_disabled": 0}
    for original in rows:
        row = dict(original)
        q = str(row.get("quality_level") or "").strip().upper()
        kind = _norm(row.get("asset_kind"))
        provenance = " ".join(str(row.get(k) or "") for k in
                              ("source_channel", "folder_path", "source_name", "source_path"))
        bad = any(x in provenance.casefold() for x in DOUYIN_MARKERS + MAIN_IMAGE_MARKERS)
        if bad or kind != "white":
            excluded["purpose_unknown_or_conflict"] += 1
            continue
        if q != "A":
            excluded["not_A"] += 1
            continue
        publish = _norm(row.get("publish_state"))
        explicitly_hidden = any(row.get(k) in (1, True, "1", "true") for k in ("is_hidden", "hidden", "is_disabled", "disabled"))
        explicitly_hidden = explicitly_hidden or publish in {"hidden", "disabled", "inactive", "off", "停用", "隐藏"}
        if explicitly_hidden:
            excluded["hidden_or_disabled"] += 1
            continue
        mid, color = str(row.get("model_id") or "").strip(), str(row.get("standard_color") or "").strip()
        if not mid or not color:
            excluded["missing_model_or_color"] += 1
            continue
        row["_model_id"], row["_color_key"] = mid, _norm(color)
        accepted.append(row)
    return accepted, {"phone_records": len(rows), "white_A_records": len(accepted), **excluded}


def asset_pools_summary(rows: list[dict]) -> dict:
    """Overlapping logical views over the same image_assets base table."""
    pools = {k: set() for k in ("white_assets", "white_A_visible", "feishu_source",
                                "douyin_marked_in_phone_base", "soft_hidden", "review_queue", "purpose_quarantine")}
    for index, row in enumerate(rows):
        identity = str(row.get("id") or f"row:{index}")
        q = str(row.get("quality_level") or "").strip().upper()
        kind = _norm(row.get("asset_kind"))
        src = " ".join(str(row.get(k) or "") for k in ("source_channel", "folder_path", "source_name", "source_path"))
        src_low = src.casefold()
        is_douyin = any(x in src_low for x in DOUYIN_MARKERS + MAIN_IMAGE_MARKERS)
        is_white = kind == "white" and not is_douyin
        hidden = any(row.get(k) in (1, True, "1", "true") for k in ("is_hidden", "hidden", "is_disabled", "disabled"))
        publish = _norm(row.get("publish_state"))
        hidden = hidden or publish in {"hidden", "disabled", "inactive", "off", "停用", "隐藏"} or q == "X"
        bad_match = _norm(row.get("match_status")) not in {"已匹配", "matched", "confirmed"}
        no_standard_link = not str(row.get("model_id") or "").strip() or not str(row.get("standard_color") or "").strip()
        if is_white: pools["white_assets"].add(identity)
        if is_white and q == "A" and not hidden and not no_standard_link: pools["white_A_visible"].add(identity)
        if "feishu" in src_low or "飞书" in src: pools["feishu_source"].add(identity)
        if is_douyin: pools["douyin_marked_in_phone_base"].add(identity)
        if hidden: pools["soft_hidden"].add(identity)
        if q == "B" or bad_match or no_standard_link: pools["review_queue"].add(identity)
        if not is_white and not is_douyin: pools["purpose_quarantine"].add(identity)
    definitions = {
        "white_assets": "明确标记为白底用途的手机图片（所有质量级别）",
        "white_A_visible": "白底用途 + A 级 + 型号和颜色已关联 + 非隐藏",
        "feishu_source": "来源字段明确标记飞书；可与白底池重叠",
        "douyin_marked_in_phone_base": "手机底表中用途/来源写有抖音或主图标记的隔离记录；不计入白底覆盖",
        "soft_hidden": "X 级或显式停用/隐藏状态",
        "review_queue": "B 级、未确认匹配或缺少标准型号/颜色",
        "purpose_quarantine": "没有明确白底用途标记，且未被识别为抖音主图",
    }
    return {"base": "image_assets(phone)", "overlapping_views": True,
            "pools": [{"id": key, "description": definitions[key], "asset_count": len(value)}
                      for key, value in pools.items()]}


def calculate(pool: dict, gallery_rows: list[dict], excluded: dict,
              *, scope: str = "all", q: str = "", offset: int = 0, limit: int = 100) -> dict:
    models = pool["models"]
    cutoff = int(pool.get("launch_year_cutoff", 2021))
    launch_year_unknown_all = sum(m.get("launch_year") is None for m in models)
    items = [m for m in models if scope == "all" or
             (m.get("launch_year") is not None and int(m["launch_year"]) >= cutoff)]
    covered, counts = {}, {}
    for row in gallery_rows:
        mid, color = row["_model_id"], row["_color_key"]
        covered.setdefault(mid, set()).add(color)
        counts[(mid, color)] = counts.get((mid, color), 0) + 1
    gaia_ids = {str(m["model_id"]) for m in models}
    gaia_colors = {str(m["model_id"]): {_norm(c) for c in m.get("colors", []) if str(c).strip()} for m in models}
    gallery_ids_outside_gaia = {row["_model_id"] for row in gallery_rows if row["_model_id"] not in gaia_ids}
    gallery_color_outside_gaia = {(row["_model_id"], row["_color_key"]) for row in gallery_rows
                                  if row["_model_id"] in gaia_ids and row["_color_key"] not in gaia_colors[row["_model_id"]]}
    gaps, per_model = [], []
    total_combos = covered_combos = models_with_any = fully_covered = 0
    scope_ids = {str(m["model_id"]) for m in items}
    for m in items:
        mid = str(m["model_id"])
        colors = sorted({str(c).strip() for c in m.get("colors", []) if str(c).strip()}, key=_norm)
        present = covered.get(mid, set()) & {_norm(c) for c in colors}
        missing = [c for c in colors if _norm(c) not in present]
        total_combos += len(colors)
        covered_combos += len(colors) - len(missing)
        models_with_any += bool(present)
        fully_covered += bool(colors) and not missing
        status = "Gaia暂无标准颜色" if not colors else ("完全缺图" if not present else ("型号×颜色有缺口" if missing else "全色覆盖"))
        record = {"model_id": mid, "brand": m.get("brand", ""), "model": m.get("model", ""),
                  "launch_year": m.get("launch_year"), "colors": colors,
                  "image_count": sum(v for (model_id, _), v in counts.items() if model_id == mid),
                  "missing_colors": missing, "model_status": status}
        per_model.append(record)
        for color in missing:
            gaps.append({**{k: record[k] for k in ("model_id", "brand", "model", "launch_year", "image_count")},
                         "color": color, "gap_type": "型号×颜色缺口"})
        if not colors and not present:
            gaps.append({**{k: record[k] for k in ("model_id", "brand", "model", "launch_year", "image_count")},
                         "color": "", "gap_type": "Gaia暂无标准颜色"})
    by_brand = {}
    models_with_color_gaps = sum(bool(x["missing_colors"]) for x in per_model)
    for m in per_model:
        row = by_brand.setdefault(m["brand"], {"brand": m["brand"], "models_total": 0,
                                                   "models_no_A": 0, "missing_combinations": 0})
        row["models_total"] += 1
        row["models_no_A"] += m["model_status"] == "完全缺图"
        row["missing_combinations"] += len(m["missing_colors"])
    by_brand = sorted(by_brand.values(), key=lambda x: (-x["missing_combinations"], -x["models_no_A"], x["brand"]))
    duplicate_combinations = [{"model_id": mid, "color_key": color, "A_image_count": count}
                              for (mid, color), count in counts.items() if count > 1 and mid in scope_ids]
    needle = _norm(q)
    if needle:
        gaps = [g for g in gaps if needle in _norm(g["brand"] + g["model"] + g["color"] + g["model_id"])]
        per_model = [m for m in per_model if needle in _norm(m["brand"] + m["model"] + m["model_id"])]
    model_gaps = [m for m in per_model if m["model_status"] != "全色覆盖"]
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "gaia_snapshot_at": pool.get("generated_at"), "gaia_source": pool.get("source"),
        "gallery_mode": "live_read_only", "scope": scope, "launch_year_cutoff": cutoff,
        "summary": {"models_total": len(items), "models_with_any_A": models_with_any,
                    "models_no_A": len(items) - models_with_any, "models_fully_color_covered": fully_covered,
                    "models_with_color_gaps": models_with_color_gaps,
                    "launch_year_unknown": sum(m.get("launch_year") is None for m in items),
                    "launch_year_unknown_all": launch_year_unknown_all,
                    "combinations_total": total_combos, "combinations_covered": covered_combos,
                    "combinations_missing": total_combos - covered_combos,
                    "combination_coverage_rate": round(covered_combos / total_combos, 4) if total_combos else None,
                    "duplicate_A_combinations": len(duplicate_combinations),
                    "duplicate_A_records_beyond_one": sum(x["A_image_count"] - 1 for x in duplicate_combinations),
                    "gallery_model_ids_not_in_gaia": len(gallery_ids_outside_gaia),
                    "gallery_color_combinations_outside_gaia": len(gallery_color_outside_gaia),
                    "by_brand": by_brand,
                    "white_pool": excluded},
        "duplicate_samples": duplicate_combinations[:100],
        "items_total": len(gaps), "items": gaps[offset:offset + limit],
        "models_total": len(model_gaps), "models": model_gaps[offset:offset + limit],
    }
