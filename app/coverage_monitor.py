"""Read-only HTTP endpoint for live Gaia-to-white-gallery coverage."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .coverage_engine import _norm, asset_pools_summary, calculate, white_pool_rows
from .model_parameters import model_params_for
from .storage_contract import read_gaia_pool, read_phone_rows

router = APIRouter(prefix="/api/v1", tags=["coverage-monitor"])
POOL_PATH = Path(os.getenv("GAIA_MODEL_POOL_PATH", "/app/gaia_runtime/gaia_model_pool.json"))
ASSET_DB = Path(os.getenv("ASSET_DB", str(Path(os.getenv("ASSET_ROOT", "/data/v03")) / "gallery.db")))


def _db():
    if not ASSET_DB.is_file():
        raise HTTPException(503, "图库只读数据库不可用")
    conn = sqlite3.connect(f"file:{ASSET_DB}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _pool() -> dict:
    return read_gaia_pool(POOL_PATH)


def _phone_rows() -> list[dict]:
    return read_phone_rows(ASSET_DB)


def _key(value: str) -> str:
    return _norm(value)


@router.get("/coverage-upload-context")
def coverage_upload_context(model_id: str, color: str):
    """Return authoritative Gaia model/color/parameter data for a missing combo."""
    mid = str(model_id or "").strip()
    requested_color = str(color or "").strip()
    if not mid or not requested_color:
        raise HTTPException(400, "必须提供 Gaia 型号 ID 和标准颜色")
    model = next((x for x in _pool()["models"] if str(x.get("model_id", "")).strip() == mid), None)
    if model is None:
        raise HTTPException(404, "Gaia 型号不存在")
    colors = [str(x).strip() for x in model.get("colors", []) if str(x).strip()]
    canonical = next((x for x in colors if _key(x) == _key(requested_color)), None)
    if canonical is None:
        raise HTTPException(422, "颜色不属于该 Gaia 型号的标准颜色")
    rows = _phone_rows()
    gallery, excluded = white_pool_rows(rows)
    if any(x.get("_model_id") == mid and x.get("_color_key") == _key(canonical) for x in gallery):
        raise HTTPException(409, "该型号颜色已有 A 级白底图，请刷新缺口清单")
    params = model_params_for(mid)
    return {
        "model_id": mid,
        "brand": str(model.get("brand", "")),
        "model": str(model.get("model", "")),
        "launch_year": model.get("launch_year"),
        "color": canonical,
        "colors": colors,
        "model_params": params,
        "parameter_status": "available" if params else "missing",
        "can_upload": bool(params),
        "gallery_snapshot": "live_read_only",
        "excluded_gallery_rows": excluded,
    }


@router.get("/coverage-monitor")
def coverage_monitor(scope: str = Query("post2021", pattern="^(all|post2021)$"),
                     q: str = "", offset: int = Query(0, ge=0),
                     limit: int = Query(100, ge=1, le=500), view: str = "combinations"):
    pool = _pool()
    rows = _phone_rows()
    gallery, excluded = white_pool_rows(rows)
    result = calculate(pool, gallery, excluded, scope=scope, q=q, offset=offset, limit=limit)
    if view == "models":
        result["items_total"] = result["models_total"]
        result["items"] = result["models"]
    return result


@router.get("/asset-pools")
def asset_pools():
    """Counts overlapping white-gallery views and the separate Douyin sub-gallery."""
    data = asset_pools_summary(_phone_rows())
    conn = _db()
    try:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        if not {"douyin_gallery_items", "douyin_gallery_folders"} <= tables:
            raise HTTPException(503, "抖音分图库表不可用，拒绝显示不完整池统计")
        images, = conn.execute("select count(*) from douyin_gallery_items").fetchone()
        folders, = conn.execute("select count(*) from douyin_gallery_folders").fetchone()
        size_bytes, = conn.execute("select coalesce(sum(size_bytes),0) from douyin_gallery_items").fetchone()
    finally:
        conn.close()
    data["base"] = "image_assets(phone) + separate Douyin sub-gallery (read-only)"
    data["pools"].append({"id": "douyin_main_separate", "description": "独立抖音分图库；不参与白底图库型号×颜色覆盖",
                          "asset_count": int(images), "folder_count": int(folders), "size_bytes": int(size_bytes),
                          "storage": "douyin_gallery_items / douyin_gallery_folders"})
    return data


@router.get("/coverage-module/model-asset-table")
def model_asset_table(q: str = "", offset: int = Query(0, ge=0),
                      limit: int = Query(25, ge=1, le=100)):
    """One row per Gaia model, using the existing trusted white coverage rules."""
    pool = _pool()
    gallery, excluded = white_pool_rows(_phone_rows())
    by_model = {}
    for row in gallery:
        by_model.setdefault(row["_model_id"], []).append(row)
    needle = _key(q)
    models = [m for m in pool["models"] if not needle or needle in _key(
        str(m.get("brand", "")) + str(m.get("model", "")) + str(m.get("model_id", "")) + " ".join(m.get("colors", [])))]
    items = []
    for model in models[offset:offset + limit]:
        mid = str(model["model_id"])
        colors = list(dict.fromkeys(str(c).strip() for c in model.get("colors", []) if str(c).strip()))
        images = [{"id": row["id"], "color": row.get("standard_color", ""),
                   "thumb_url": f"/api/v1/images/{row['id']}/file?w=160",
                   "file_url": f"/api/v1/images/{row['id']}/file"} for row in by_model.get(mid, [])]
        present = {_key(image["color"]) for image in images}
        items.append({**model, "model_id": mid, "colors": colors, "images": images,
                      "missing_colors": [color for color in colors if _key(color) not in present],
                      "model_params": model_params_for(mid)})
    return {"total": len(models), "items": items, "gaia_snapshot_at": pool.get("generated_at"),
            "gallery_mode": "live_read_only", "excluded": excluded}
