"""A/B/C pool management for existing white-background phone assets.

Storage remains backward compatible: C is displayed as C but persisted as X.
Every decision applies to one image-asset/model relation, never to a SHA globally.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .storage_contract import ASSET_DB, ASSET_ROOT
from .business_review import _require_login
from .storage_contract import read_gaia_pool
from .coverage_engine import _norm

router = APIRouter(prefix="/api/v1/pool-management", tags=["image pools"])
POOL_TO_LEVEL = {"A": "A", "B": "B", "C": "X"}
LEVEL_TO_POOL = {v: k for k, v in POOL_TO_LEVEL.items()}
WHITE_KINDS = {"white"}
DOUYIN_MARKERS = ("抖音", "douyin", "tiktok", "主图", "marketing", "feed_image")


def _readonly_db() -> sqlite3.Connection:
    if not ASSET_DB.is_file():
        raise HTTPException(503, "图片资产库尚未初始化")
    conn = sqlite3.connect(f"file:{ASSET_DB}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _write_db() -> sqlite3.Connection:
    if not ASSET_DB.is_file():
        raise HTTPException(503, "图片资产库尚未初始化")
    conn = sqlite3.connect(ASSET_DB, timeout=20, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _is_white_asset(row: dict) -> bool:
    if str(row.get("category") or "phone").strip().lower() != "phone":
        return False
    if _norm(row.get("asset_kind")) not in {_norm(x) for x in WHITE_KINDS}:
        return False
    provenance = " ".join(str(row.get(k) or "") for k in
                          ("source_channel", "folder_path", "source_name", "source_path"))
    return not any(marker in provenance.casefold() for marker in DOUYIN_MARKERS)


def _pool_for_level(level: str | None) -> str:
    # Historical X was also used for non-keeper duplicates and routing fixes.
    # Without explicit evidence that the image itself needs correction, keep it
    # reviewable in B. Only known white-margin failures belong in C by default.
    return {"A": "A", "B": "B", "X": "B"}.get(str(level or "B").strip().upper(), "B")


def _pool_for_asset(row: dict) -> str:
    level = str(row.get("quality_level") or "B").strip().upper()
    if level != "X":
        return _pool_for_level(level)
    source = str(row.get("quality_source") or "").casefold()
    reason = str(row.get("quality_reason") or "").casefold()
    if source == "business_pool_console":
        return "C"
    if (source == "white_background_standard_v1.1"
            and "大主体" in reason and "留白不足" in reason):
        return "C"
    return "B"


def _public_asset(row: sqlite3.Row) -> dict:
    d = dict(row)
    aid = str(d["id"])
    return {
        "asset_id": aid,
        "brand": d.get("display_brand") or d.get("brand_dir") or d.get("brand_name") or "",
        "model": d.get("model_name") or d.get("model_dir") or "",
        "model_id": d.get("model_id") or "",
        "color": d.get("standard_color") or d.get("gaia_color") or d.get("color") or "",
        "quality_level": d.get("quality_level") or "B",
        "pool": _pool_for_asset(d),
        "reason": d.get("quality_reason") or "",
        "quality_source": d.get("quality_source") or "",
        "updated_at": d.get("quality_updated_at") or "",
        "width": d.get("width"), "height": d.get("height"),
        "format": d.get("format") or "", "size_bytes": d.get("size_bytes"),
        "sha256": d.get("sha256") or "",
        "match_status": d.get("match_status") or "",
        "source_channel": d.get("source_channel") or "",
        "product_page_url": _source_page(d.get("spec_json")),
        "thumb_url": f"/api/v1/pool-management/assets/{aid}/image",
        "file_url": f"/api/v1/pool-management/assets/{aid}/image",
    }


def _legacy_rows(conn: sqlite3.Connection) -> list[dict]:
    """Expose old review-queue relations in the new pools without dropping them.

    Approved rows already exist as image_assets and are deliberately excluded.
    Pending/deferred/rejected are old workflow states, not proof of permanent
    unusability, so all unresolved legacy relations start in B. A human may move
    a clearly unusable relation to C. Later moves are relation-scoped overrides.
    """
    table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_review_items'").fetchone()
    if not table:
        return []
    rows = conn.execute("""SELECT i.*,
        (SELECT e.to_status FROM business_review_events e
         WHERE e.item_id='legacy:'||i.item_id AND e.action='pool_override'
         ORDER BY e.event_id DESC LIMIT 1) AS pool_override
        FROM business_review_items i
        WHERE i.status IN ('pending','deferred','rejected')
        ORDER BY i.imported_at,i.item_id""").fetchall()
    out = []
    for row in rows:
        d = dict(row)
        pool = str(d.get("pool_override") or "B").upper()
        if pool not in {"B", "C"}:
            pool = "B"
        allowed = _json_list(d.get("allowed_colors_json"))
        evidence = _json_dict(d.get("ai_evidence_json"))
        out.append({
            "asset_id": "legacy:" + str(d["item_id"]), "legacy_item_id": str(d["item_id"]),
            "legacy": True, "brand": d.get("brand") or "", "model": d.get("model") or "",
            "model_id": d.get("gaia_model_id") or "", "color": d.get("selected_color") or "",
            "allowed_colors": allowed, "quality_level": "X" if pool == "C" else "B",
            "pool": pool, "reason": ("历史判决：" + str(d.get("reason_detail") or "未记录原因")),
            "quality_source": "legacy_business_review", "updated_at": d.get("decided_at") or d.get("imported_at") or "",
            "width": _evidence_value(d, evidence, "width"), "height": _evidence_value(d, evidence, "height"),
            "format": _evidence_value(d, evidence, "format"), "size_bytes": _evidence_value(d, evidence, "size_bytes"),
            "sha256": d.get("image_sha256") or "", "match_status": "待复核",
            "source_channel": d.get("source_name") or "", "product_page_url": d.get("product_page_url") or "",
            "thumb_url": f"/api/v1/business-review/items/{d['item_id']}/image",
            "file_url": f"/api/v1/business-review/items/{d['item_id']}/image",
        })
    return out


def _json_list(raw: str | None) -> list:
    import json
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _json_dict(raw: str | None) -> dict:
    import json
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _evidence_value(row: dict, evidence: dict, key: str):
    source = evidence.get("source_evidence", {}) if isinstance(evidence.get("source_evidence"), dict) else {}
    return source.get(key) or evidence.get(key) or ""


def _source_page(raw: str | None) -> str:
    import json
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return ""
    for key in ("product_page_url", "image_page_url", "source_url"):
        val = data.get(key)
        if isinstance(val, str) and val.startswith(("https://", "http://")):
            return val
    return ""


def _white_assets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute("""SELECT * FROM image_assets
        WHERE lower(coalesce(category,'phone'))='phone'
          AND asset_kind = 'white' 
        ORDER BY coalesce(display_brand,brand_dir,brand_name,''), coalesce(model_name,model_dir,''), id""").fetchall()
    return [r for r in rows if _is_white_asset(dict(r))]


@router.get("/summary")
def pool_summary(request: Request):
    _require_login(request)
    with closing(_readonly_db()) as conn:
        rows = _white_assets(conn)
        legacy = _legacy_rows(conn)
    counts = {pool: 0 for pool in POOL_TO_LEVEL}
    for row in rows:
        counts[_pool_for_asset(dict(row))] += 1
    for row in legacy:
        counts[row["pool"]] += 1
    return {"pools": counts, "total": sum(counts.values()), "display_mapping": {"C": "X"},
            "count_unit": "image-model relations; shared SHA across models counts once per relation"}


@router.get("/{pool}/items")
def list_pool(pool: str, request: Request, q: str = "",
              limit: int = Query(40, ge=1, le=100), offset: int = Query(0, ge=0)):
    _require_login(request)
    pool = pool.upper()
    if pool not in POOL_TO_LEVEL:
        raise HTTPException(400, "池子只能是 A、B 或 C")
    needle = q.strip().casefold()
    with closing(_readonly_db()) as conn:
        rows = _white_assets(conn)
        legacy = _legacy_rows(conn)
    selected = [_public_asset(r) for r in rows if _pool_for_asset(dict(r)) == pool]
    selected.extend(r for r in legacy if r["pool"] == pool)
    if needle:
        selected = [r for r in selected if needle in " ".join(str(r.get(k) or "")
                    for k in ("brand", "model", "model_id", "color", "reason", "sha256")).casefold()]
    selected.sort(key=lambda r: (str(r.get("brand") or "").casefold(), str(r.get("model") or "").casefold(), str(r.get("asset_id") or "")))
    total = len(selected)
    return {"pool": pool, "total": total, "limit": limit, "offset": offset,
            "items": selected[offset:offset + limit]}


@router.get("/assets/{asset_id}/history")
def asset_history(asset_id: str, request: Request):
    _require_login(request)
    with closing(_readonly_db()) as conn:
        events = conn.execute("""SELECT action,from_status,to_status,reviewer_label,
            reviewer_note,created_at FROM business_review_events
            WHERE item_id=? ORDER BY event_id DESC LIMIT 100""", ("legacy:" + asset_id if asset_id.startswith("legacy:") else "asset:" + asset_id,)).fetchall()
    return {"asset_id": asset_id, "events": [dict(e) for e in events]}


@router.get("/assets/{asset_id}/image")
def pool_asset_image(asset_id: str, request: Request):
    _require_login(request)
    with closing(_readonly_db()) as conn:
        row = conn.execute("SELECT * FROM image_assets WHERE id=?", (asset_id,)).fetchone()
    if not row or not _is_white_asset(dict(row)):
        raise HTTPException(404, "白底图库图片不存在")
    local = str(row["local_path"] or "")
    path = (ASSET_ROOT / local).resolve()
    if ASSET_ROOT.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "图片文件缺失")
    ext = str(row["format"] or "").lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=mime,
                        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"})


@router.post("/assets/{asset_id}/pool")
def move_asset(asset_id: str, payload: dict, request: Request):
    _require_login(request)
    target_pool = str(payload.get("pool", "")).upper()
    reason = str(payload.get("reason", "")).strip()[:1000]
    if target_pool not in POOL_TO_LEVEL:
        raise HTTPException(400, "目标池只能是 A、B 或 C")
    if not reason:
        raise HTTPException(422, "请填写本次调整原因")
    target_level = POOL_TO_LEVEL[target_pool]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    actor = str(getattr(request.state, "role", "图库审核账户"))[:120]
    if asset_id.startswith("legacy:"):
        item_id = asset_id.split(":", 1)[1]
        with closing(_write_db()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = conn.execute("SELECT * FROM business_review_items WHERE item_id=?", (item_id,)).fetchone()
            if not item or item["status"] == "approved":
                conn.rollback(); raise HTTPException(404, "历史候选关系不存在或已进入 A 池")
            events = conn.execute("SELECT to_status FROM business_review_events WHERE item_id=? AND action='pool_override' ORDER BY event_id DESC LIMIT 1", ("legacy:" + item_id,)).fetchone()
            from_pool = str(events[0] if events else "B").upper()
            if from_pool == target_pool:
                conn.commit(); return {"ok": True, "already_in_pool": True, "pool": target_pool}
            if target_pool == "A":
                if from_pool == "C":
                    conn.rollback(); raise HTTPException(409, "C 池记录须先回到 B 池复核")
                colors = _json_list(item["allowed_colors_json"])
                conn.commit()
                if not colors:
                    raise HTTPException(422, "没有 Gaia 标准颜色，不能进入 A")
                # Reopen a legacy rejected record as deferred before the existing
                # approval endpoint applies its exact color, source-file and combo checks.
                if item["status"] == "rejected":
                    with closing(_write_db()) as reopen:
                        reopen.execute("BEGIN IMMEDIATE")
                        reopen.execute("UPDATE business_review_items SET status='deferred' WHERE item_id=? AND status='rejected'", (item_id,))
                        reopen.execute("INSERT INTO business_review_events (item_id,action,from_status,to_status,reviewer_id,reviewer_label,reviewer_note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (item_id, "reopen_for_pool_review", "rejected", "deferred", None, actor, reason, now))
                        reopen.commit()
                from . import business_review
                result = business_review.decide(item_id, {"decision":"approve", "color":str(payload.get("color") or "").strip(), "note":reason}, request)
                return {"ok": True, "from_pool": from_pool, "pool": "A", "reason": reason, "asset_id": result.get("asset_id")}
            conn.execute("INSERT INTO business_review_events (item_id,action,from_status,to_status,reviewer_id,reviewer_label,reviewer_note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("legacy:" + item_id, "pool_override", from_pool, target_pool, None, actor, reason, now))
            conn.commit()
        return {"ok": True, "from_pool": from_pool, "pool": target_pool, "reason": reason}
    with closing(_write_db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM image_assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            conn.rollback(); raise HTTPException(404, "图库图片不存在")
        current = dict(row)
        if not _is_white_asset(current):
            conn.rollback(); raise HTTPException(422, "此功能只管理手机白底组图片")
        from_level = str(current.get("quality_level") or "B").upper()
        from_pool = _pool_for_asset(current)
        if from_pool == target_pool:
            conn.commit()
            return {"ok": True, "already_in_pool": True, "pool": target_pool}
        if from_pool == "C" and target_pool == "A":
            conn.rollback(); raise HTTPException(409, "C 池图片需先回到 B 池复核，再进入 A")
        if target_pool == "A":
            if (not str(current.get("model_id") or "").strip()
                    or not str(current.get("standard_color") or "").strip()
                    or str(current.get("match_status") or "").strip() not in {"已匹配", "matched", "confirmed"}):
                conn.rollback(); raise HTTPException(422, "进入 A 池前须补齐准确型号、标准颜色和已匹配状态")
            gaia_model = next((m for m in read_gaia_pool().get("models", [])
                               if str(m.get("model_id", "")).strip() == str(current["model_id"]).strip()), None)
            gaia_colors = [str(c).strip() for c in (gaia_model or {}).get("colors", []) if str(c).strip()]
            if not gaia_model or str(current["standard_color"]).strip() not in gaia_colors:
                conn.rollback(); raise HTTPException(422, "进入 A 池前，颜色必须精确命中该型号当前 Gaia 标准色")
            if min(int(current.get("width") or 0), int(current.get("height") or 0)) < 600:
                conn.rollback(); raise HTTPException(422, "进入 A 池前，图片最短边必须至少 600 像素")
            color = str(current.get("standard_color") or "").strip()
            duplicate = conn.execute("""SELECT id FROM image_assets
                WHERE id<>? AND lower(coalesce(category,'phone'))='phone'
                  AND quality_level='A' AND model_id=?
                  AND lower(trim(standard_color))=lower(trim(?)) LIMIT 1""",
                (asset_id, str(current["model_id"]), color)).fetchone()
            if duplicate:
                conn.rollback(); raise HTTPException(409, f"该型号 × 颜色已有 A 图（{duplicate['id']}），请先择优处理")
        conn.execute("""UPDATE image_assets SET quality_level=?,quality_reason=?,
            quality_source='business_pool_console',quality_updated_at=? WHERE id=?""",
            (target_level, reason, now, asset_id))
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_review_events'").fetchone():
            conn.rollback(); raise HTTPException(503, "审核事件表尚未初始化，未更改图片池状态")
        conn.execute("""INSERT INTO business_review_events
            (item_id,action,from_status,to_status,reviewer_id,reviewer_label,reviewer_note,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            ("asset:" + asset_id, "move_pool", from_level, target_level, None, actor,
             reason, now))
        conn.commit()
    return {"ok": True, "from_pool": from_pool, "pool": target_pool, "reason": reason}

