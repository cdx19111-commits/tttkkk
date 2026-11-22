"""Authenticated B-pool decision desk backed by the production asset database."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from . import categories
from .storage_contract import read_phone_rows
from .coverage_engine import _norm, white_pool_rows
from .storage_contract import ASSET_DB, ASSET_ROOT

router = APIRouter(prefix="/api/v1/business-review", tags=["business review"])
ASSET_DIR = ASSET_ROOT / "business_review_assets"
BATCH_ID = "907_b_pool_20261117"
VALID_STATES = {"pending", "approved", "rejected", "deferred"}
VALID_DECISIONS = {"approve", "reject", "defer"}

REASON_LABELS = {
    "color_uncertain": "颜色判断冲突或无法确定",
    "model_suspicion": "型号归属存疑",
    "image_quality": "图片质量或白底要求待核",
    "gaia_color_suspicion": "Gaia 颜色标准待核",
    "source_conflict": "图源标注与视觉判断冲突",
    "rights_scope": "图源使用范围待确认",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(ASSET_DB, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


def init_schema() -> None:
    """Create only additive review tables; never alter or rewrite gallery rows."""
    with closing(_conn()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS business_review_batches (
            batch_id TEXT PRIMARY KEY, source_name TEXT NOT NULL,
            relation_count INTEGER NOT NULL, unique_images INTEGER NOT NULL,
            imported_at TEXT NOT NULL, source_snapshot TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS business_review_items (
            item_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
            relation_key TEXT NOT NULL UNIQUE, image_sha256 TEXT NOT NULL,
            image_relpath TEXT NOT NULL, image_missing INTEGER NOT NULL DEFAULT 0,
            brand TEXT NOT NULL, model TEXT NOT NULL, gaia_model_id TEXT NOT NULL,
            allowed_colors_json TEXT NOT NULL,
            reason_types_json TEXT NOT NULL, reason_detail TEXT NOT NULL,
            source_name TEXT NOT NULL, source_record_path TEXT NOT NULL,
            source_url TEXT NOT NULL, product_page_url TEXT NOT NULL,
            ai_evidence_json TEXT NOT NULL, source_evidence_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
              CHECK(status IN ('pending','approved','rejected','deferred')),
            selected_color TEXT NOT NULL DEFAULT '', official_asset_id TEXT NOT NULL DEFAULT '',
            reviewer_id TEXT, reviewer_label TEXT NOT NULL DEFAULT '',
            reviewer_note TEXT NOT NULL DEFAULT '', decided_at TEXT NOT NULL DEFAULT '',
            imported_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_business_review_status
          ON business_review_items(status, imported_at, item_id);
        CREATE INDEX IF NOT EXISTS idx_business_review_model
          ON business_review_items(brand, model, status);
        CREATE TABLE IF NOT EXISTS business_review_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL,
            action TEXT NOT NULL, from_status TEXT NOT NULL, to_status TEXT NOT NULL,
            selected_color TEXT NOT NULL DEFAULT '', official_asset_id TEXT NOT NULL DEFAULT '',
            reviewer_id TEXT, reviewer_label TEXT NOT NULL,
            reviewer_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_business_review_events_item
          ON business_review_events(item_id, event_id);
        """)


def _require_login(request: Request) -> None:
    # The outer SessionAuthMiddleware blocks unauthenticated requests. Keep a second
    # explicit guard so these routes stay fail-closed if mounted elsewhere later.
    if getattr(request.state, "role", "") not in ("business", "review"):
        raise HTTPException(401, "需要图库授权登录")


def _loads(raw: str, fallback):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _reason_types(row: dict) -> list[str]:
    why = "|".join(str(row.get(k, "") or "") for k in (
        "rejection_reasons", "reason", "result_code", "result_evidence"))
    out = []
    if any(s in why for s in ("deepseek_qwen_color_disagreement", "color_unclear",
                              "visual_color_unclear", "color_confidence", "No per-image color")):
        out.append("color_uncertain")
    if any(s in why for s in ("model_mismatch", "model_evidence", "model_link", "cross_model_sha")):
        out.append("model_suspicion")
    if any(s in why for s in ("white_background", "image_quality", "image_not_clear",
                              "body_not_confirmed", "single_product", "marketing_or_watermark",
                              "image_quality_gate")):
        out.append("image_quality")
    if (any(s in why for s in ("gaia_sku", "color_not_in_exact", "no_gaia_sku_colors"))
            or not str(row.get("gaia_sku_colors_current", "") or "").strip()):
        out.append("gaia_color_suspicion")
    if any(s in why for s in ("source_color_label_conflicts", "source label does not exactly")):
        out.append("source_conflict")
    if not out:
        out.append("color_uncertain")
    # The full batch's source-use rights are explicitly unconfirmed. This is kept as
    # a separate visible concern; acceptance is internal-only and never publishes.
    out.append("rights_scope")
    return list(dict.fromkeys(out))


UPLOAD_MAX_BYTES = 20 * 1024 * 1024
UPLOAD_MIME_FORMATS = {"image/jpeg": ("JPEG", "jpg"),
                       "image/png": ("PNG", "png"),
                       "image/webp": ("WEBP", "webp")}
COVERAGE_API_URL = os.getenv("COVERAGE_API_URL", "http://coverage-api:8000").rstrip("/")


def _safe_upload_name(value: str) -> str:
    value = str(value or "").replace("\\", "/").split("/")[-1]
    value = "".join(ch for ch in value if ord(ch) >= 32 and ch not in "<>:\"|?*").strip()
    return (value or "business-upload")[:180]


async def _coverage_upload_context(request: Request, model_id: str, color: str) -> dict:
    import httpx
    role = getattr(request.state, "role", "")
    if role not in ("business", "review"):
        raise HTTPException(401, "需要图库授权登录")
    client = getattr(request.app.state, "api_proxy_client", None)
    owned = client is None
    if owned:
        client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0), trust_env=False)
    try:
        response = await client.get(
            f"{COVERAGE_API_URL}/api/v1/coverage-upload-context",
            params={"model_id": model_id, "color": color},
            headers={"x-gallery-role": role},
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "覆盖数据校验失败")
            except Exception:
                detail = "覆盖数据校验失败"
            raise HTTPException(response.status_code, str(detail))
        payload = response.json()
        if payload.get("model_id") != model_id or payload.get("color") != color:
            raise HTTPException(502, "覆盖服务返回的型号或颜色不一致")
        if not payload.get("can_upload"):
            raise HTTPException(409, "该型号尚无可用参数数据，暂不能提交图片")
        return payload
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(503, "覆盖数据服务暂不可用，未接收图片") from exc
    finally:
        if owned:
            await client.aclose()


def _inspect_upload_image(path: Path, declared_mime: str) -> tuple[str, int, int]:
    try:
        from PIL import Image
        with Image.open(path) as image:
            fmt = str(image.format or "").upper()
            width, height = image.size
            image.verify()
        expected = UPLOAD_MIME_FORMATS.get(declared_mime, ("", ""))[0]
        if fmt not in {"JPEG", "PNG", "WEBP"} or fmt != expected:
            raise HTTPException(415, "图片格式与文件内容不一致，仅支持 JPEG、PNG、WebP")
        if width < 600 or height < 600:
            raise HTTPException(422, "图片分辨率不足，宽和高都必须至少为 600 像素")
        return fmt, width, height
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "图片无法解码或文件已损坏") from exc


def _ensure_combo_uncovered(model_id: str, color: str) -> None:
    """Preserve the one-A-image-per-exact-Gaia-model/color business invariant."""
    rows, _ = white_pool_rows(read_phone_rows(ASSET_DB))
    if any(row.get("_model_id") == str(model_id).strip()
           and row.get("_color_key") == _norm(color) for row in rows):
        raise HTTPException(409, "该型号颜色组合已有 A 级白底图，不能再新增第二张")


@router.post("/uploads", status_code=201)
async def upload_missing_combo_image(request: Request, model_id: str, color: str,
                                     filename: str = Query(default="business-upload")):
    """Accept one white-image candidate for an exact Gaia gap and queue human review."""
    _require_login(request)
    model_id = str(model_id or "").strip()
    color = str(color or "").strip()
    if not model_id or not color:
        raise HTTPException(400, "必须提供 Gaia 型号 ID 和颜色")
    context = await _coverage_upload_context(request, model_id, color)
    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in UPLOAD_MIME_FORMATS:
        raise HTTPException(415, "仅支持 JPEG、PNG、WebP 图片")
    length = request.headers.get("content-length", "")
    if length:
        try:
            if int(length) > UPLOAD_MAX_BYTES:
                raise HTTPException(413, "图片不能超过 20 MB")
        except ValueError:
            raise HTTPException(400, "上传长度无效")

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    asset_root = ASSET_ROOT.resolve()
    asset_dir = ASSET_DIR.resolve()
    if asset_root not in asset_dir.parents:
        raise HTTPException(500, "候选图片目录配置不安全")
    upload_token = secrets.token_hex(16)
    temp_path = asset_dir / f".{upload_token}.part"
    digest = hashlib.sha256()
    size = 0
    final_path = None
    created_file = False
    try:
        with temp_path.open("xb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > UPLOAD_MAX_BYTES:
                    raise HTTPException(413, "图片不能超过 20 MB")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "没有收到图片内容")
        fmt, width, height = _inspect_upload_image(temp_path, mime)
        sha = digest.hexdigest()
        ext = UPLOAD_MIME_FORMATS[mime][1]
        final_relpath = f"business_review_assets/{sha}.{ext}"
        final_path = asset_root / final_relpath
        if asset_root not in final_path.resolve().parents:
            raise HTTPException(500, "图片存储路径无效")
        item_id = "upload_" + uuid.uuid4().hex
        relation_key = hashlib.sha256(
            (model_id + "\0" + color + "\0" + sha).encode("utf-8")).hexdigest()
        batch_id = "business_upload_" + item_id
        now = _now()
        original_name = _safe_upload_name(filename)
        source_snapshot = {
            "source": "business_upload", "model_id": model_id, "color": color,
            "uploaded_at": now,
        }
        evidence = {
            "upload_source": "business_user", "filename": original_name,
            "mime_type": mime, "format": fmt, "width": width, "height": height,
            "size_bytes": size, "sha256": sha,
            "model_params": context.get("model_params", []),
        }
        with closing(_conn()) as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT item_id,status FROM business_review_items WHERE relation_key=?",
                (relation_key,)).fetchone()
            if existing:
                c.rollback()
                return {"ok": True, "duplicate": True, "item_id": existing["item_id"],
                        "status": existing["status"], "message": "相同图片已提交过"}
            c.execute("""INSERT OR IGNORE INTO business_review_batches
                (batch_id,source_name,relation_count,unique_images,imported_at,source_snapshot)
                VALUES(?,?,?,?,?,?)""",
                (batch_id, "business_upload", 1, 1, now,
                 json.dumps(source_snapshot, ensure_ascii=False)))
            if final_path.exists():
                old_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()
                if old_hash != sha:
                    c.rollback()
                    raise HTTPException(409, "存储中的同名图片校验失败")
            else:
                os.replace(temp_path, final_path)
                created_file = True
            c.execute("""INSERT INTO business_review_items
                (item_id,batch_id,relation_key,image_sha256,image_relpath,image_missing,
                 brand,model,gaia_model_id,allowed_colors_json,reason_types_json,reason_detail,
                 source_name,source_record_path,source_url,product_page_url,
                 ai_evidence_json,source_evidence_json,status,imported_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, batch_id, relation_key, sha, final_relpath, 0,
                 context["brand"], context["model"], model_id,
                 json.dumps([color], ensure_ascii=False), json.dumps(["image_quality"]),
                 "业务方从缺口清单上传；待复核型号、颜色、白底和主体完整度。",
                 "business_upload", original_name, "internal_user_upload", "",
                 "{}", json.dumps(evidence, ensure_ascii=False), "pending", now))
            c.commit()
        return {"ok": True, "duplicate": False, "item_id": item_id, "status": "pending",
                "model_id": model_id, "brand": context["brand"], "model": context["model"],
                "color": color, "message": "图片已提交至业务决策台待审核；审核通过后才计入 A 级覆盖"}
    except HTTPException:
        if created_file and final_path is not None:
            final_path.unlink(missing_ok=True)
        raise
    except sqlite3.IntegrityError as exc:
        if created_file and final_path is not None:
            final_path.unlink(missing_ok=True)
        raise HTTPException(409, "该型号颜色图片已提交，请刷新后查看审核状态") from exc
    except Exception:
        if created_file and final_path is not None:
            final_path.unlink(missing_ok=True)
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def import_manifest(manifest_path: str) -> dict:
    """Idempotently import the reviewed source snapshot; image paths are relative."""
    init_schema()
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    items = payload["items"]
    now = _now()
    inserted = 0
    missing = 0
    with closing(_conn()) as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("""INSERT OR IGNORE INTO business_review_batches
            (batch_id,source_name,relation_count,unique_images,imported_at,source_snapshot)
            VALUES(?,?,?,?,?,?)""", (payload["batch_id"], payload["source_name"],
              len(items), payload["unique_images"], now, payload["source_snapshot"]))
        for item in items:
            rel = str(item["image_relpath"])
            path = (ASSET_ROOT / rel).resolve()
            if ASSET_ROOT.resolve() not in path.parents or not re.fullmatch(
                    r"business_review_assets/[a-f0-9]{64}\.[a-z0-9]{1,5}", rel):
                raise ValueError("invalid image path")
            absent = not path.is_file()
            if not absent:
                h = hashlib.sha256()
                with path.open("rb") as image:
                    for chunk in iter(lambda: image.read(1024 * 1024), b""):
                        h.update(chunk)
                absent = h.hexdigest() != item["image_sha256"]
            missing += int(absent)
            reason_types = _reason_types(item["source_record"])
            evidence = {"deepseek": item.get("deepseek", {}), "qwen": item.get("qwen", {}),
                        "classification": item.get("classification", {})}
            source = item.get("source", {})
            cur = c.execute("""INSERT OR IGNORE INTO business_review_items
                (item_id,batch_id,relation_key,image_sha256,image_relpath,image_missing,
                 brand,model,gaia_model_id,allowed_colors_json,reason_types_json,reason_detail,
                 source_name,source_record_path,source_url,product_page_url,
                 ai_evidence_json,source_evidence_json,status,imported_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item["item_id"], payload["batch_id"], item["relation_key"],
                 item["image_sha256"], rel, int(absent), item["brand"], item["model"],
                 item["gaia_model_id"], json.dumps(item["allowed_colors"], ensure_ascii=False),
                 json.dumps(reason_types, ensure_ascii=False), item["reason_detail"],
                 source.get("name", ""), source.get("record_path", ""),
                 source.get("url", ""), source.get("product_page_url", ""),
                 json.dumps(evidence, ensure_ascii=False),
                 json.dumps(source.get("original_record", {}), ensure_ascii=False),
                 "pending", now))
            inserted += cur.rowcount
        c.commit()
    return {"inserted": inserted, "existing": len(items) - inserted,
            "relations": len(items), "unique_images": payload["unique_images"],
            "missing_images": missing}


def _public_item(r: sqlite3.Row, detail: bool = False) -> dict:
    d = dict(r)
    allowed = _loads(d.pop("allowed_colors_json", "[]"), [])
    reasons = _loads(d.pop("reason_types_json", "[]"), [])
    ai = _loads(d.pop("ai_evidence_json", "{}"), {})
    source = _loads(d.pop("source_evidence_json", "{}"), {})
    d.pop("relation_key", None)
    d.pop("image_relpath", None)
    d.pop("batch_id", None)
    d["allowed_colors"] = allowed
    d["reason_types"] = reasons
    d["reason_labels"] = [REASON_LABELS.get(x, x) for x in reasons]
    d["ai_evidence"] = ai
    d.pop("source_evidence_json", None)
    if detail:
        d["source_evidence"] = source
        d["usage_notice"] = "来源素材权限尚未确认；确认后只进入登录可见的内部图库，不公开发布。"
    else:
        d["deepseek_color"] = (ai.get("deepseek") or {}).get("color", "")
        d["qwen_color"] = (ai.get("qwen") or {}).get("color", "")
        d.pop("source_evidence", None)
        d.pop("ai_evidence", None)
        d["thumb_url"] = f"/api/v1/business-review/items/{d['item_id']}/image"
    return d


@router.get("/summary")
def summary(request: Request):
    _require_login(request)
    with closing(_conn()) as c:
        vals = {r["status"]: r["n"] for r in c.execute(
            "SELECT status,count(*) n FROM business_review_items GROUP BY status")}
        batch = c.execute("SELECT relation_count,unique_images,source_snapshot FROM business_review_batches WHERE batch_id=?",
                          (BATCH_ID,)).fetchone()
        missing = c.execute("SELECT count(*) FROM business_review_items WHERE image_missing=1").fetchone()[0]
    return {"pending": vals.get("pending", 0), "approved": vals.get("approved", 0),
            "rejected": vals.get("rejected", 0), "deferred": vals.get("deferred", 0),
            "imported_relations": batch[0] if batch else 0,
            "unique_images": batch[1] if batch else 0,
            "missing_images": missing,
            "auto_resolved": 0,
            "auto_note": "已有视觉结论均未同时满足冻结的来源、型号、质量和颜色准入条件；保留 AI 建议供审核，不自动入库。"}


@router.get("/items")
def list_items(request: Request, status: str = "pending", brand: str = "",
               model: str = "", reason: str = "", limit: int = Query(40, ge=1, le=100),
               offset: int = Query(0, ge=0)):
    _require_login(request)
    if status not in (*VALID_STATES, "all"):
        raise HTTPException(400, "审核状态无效")
    where, args = [], []
    if status != "all":
        where.append("status=?"); args.append(status)
    if brand.strip():
        where.append("brand LIKE ?"); args.append("%" + brand.strip() + "%")
    if model.strip():
        where.append("model LIKE ?"); args.append("%" + model.strip() + "%")
    if reason:
        if reason not in REASON_LABELS:
            raise HTTPException(400, "存疑类型无效")
        where.append("reason_types_json LIKE ?"); args.append("%\"" + reason + "\"%")
    clause = " WHERE " + " AND ".join(where) if where else ""
    with closing(_conn()) as c:
        total = c.execute("SELECT count(*) FROM business_review_items" + clause, args).fetchone()[0]
        rows = c.execute("SELECT * FROM business_review_items" + clause +
                          " ORDER BY imported_at,item_id LIMIT ? OFFSET ?",
                          args + [limit, offset]).fetchall()
    return {"total": total, "limit": limit, "offset": offset,
            "items": [_public_item(r) for r in rows]}


@router.get("/items/{item_id}")
def get_item(item_id: str, request: Request):
    _require_login(request)
    with closing(_conn()) as c:
        r = c.execute("SELECT * FROM business_review_items WHERE item_id=?", (item_id,)).fetchone()
    if not r:
        raise HTTPException(404, "待审核记录不存在")
    return _public_item(r, detail=True)


@router.get("/items/{item_id}/image")
def get_item_image(item_id: str, request: Request):
    _require_login(request)
    with closing(_conn()) as c:
        r = c.execute("SELECT image_relpath,image_missing FROM business_review_items WHERE item_id=?",
                       (item_id,)).fetchone()
    if not r:
        raise HTTPException(404, "待审核记录不存在")
    path = (ASSET_ROOT / r["image_relpath"]).resolve()
    if ASSET_ROOT.resolve() not in path.parents or r["image_missing"] or not path.is_file():
        raise HTTPException(404, "原图缺失，已标记待补图")
    ext = path.suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=mime,
                        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"})


@router.post("/items/{item_id}/decision")
def decide(item_id: str, payload: dict, request: Request):
    _require_login(request)
    decision = str(payload.get("decision", ""))
    note = str(payload.get("note", ""))[:1000]
    color = str(payload.get("color", "")).strip()
    if decision not in VALID_DECISIONS:
        raise HTTPException(400, "审核操作无效")
    target = {"approve": "approved", "reject": "rejected", "defer": "deferred"}[decision]
    if decision == "approve" and not color:
        raise HTTPException(400, "请选择 Gaia 标准颜色")
    now = _now()
    # Role is known, but this shared login does not expose a reliable personal ID.
    actor_id = None
    actor_label = "图库授权账户（系统未提供个人身份）"
    asset_id = ""
    with closing(_conn()) as c:
        c.execute("BEGIN IMMEDIATE")
        item = c.execute("SELECT * FROM business_review_items WHERE item_id=?", (item_id,)).fetchone()
        if not item:
            c.rollback(); raise HTTPException(404, "待审核记录不存在")
        current = item["status"]
        if current == target and (decision != "approve" or item["selected_color"] == color):
            c.commit()
            return {"ok": True, "already_saved": True, "status": current,
                    "asset_id": item["official_asset_id"] or None}
        if current in ("approved", "rejected"):
            c.rollback(); raise HTTPException(409, "该记录已完成审核，不能重复提交不同决定")
        if decision == "approve":
            allowed = _loads(item["allowed_colors_json"], [])
            if color not in allowed:
                c.rollback(); raise HTTPException(422, "所选颜色不在该型号的 Gaia 标准颜色列表中")
            rel = item["image_relpath"]
            img = (ASSET_ROOT / rel).resolve()
            if ASSET_ROOT.resolve() not in img.parents or not img.is_file() or item["image_missing"]:
                c.rollback(); raise HTTPException(409, "原图缺失，暂不能入库")
            # Do not insert the same image + Gaia model + standard color twice.
            existing = c.execute("""SELECT id FROM image_assets
                WHERE sha256=? AND model_id=? AND
                coalesce(nullif(standard_color,''),nullif(gaia_color,''),color,'')=? LIMIT 1""",
                (item["image_sha256"], item["gaia_model_id"], color)).fetchone()
            if existing:
                asset_id = existing[0]
            else:
                _ensure_combo_uncovered(item["gaia_model_id"], color)
                asset_id = "br_" + hashlib.sha256(
                    (item["image_sha256"] + "\0" + item["gaia_model_id"] + "\0" + color).encode()
                ).hexdigest()[:32]
                filename = Path(item["source_record_path"] or img.name).name or img.name
                ext = img.suffix.lower().lstrip(".") or "jpg"
                size = img.stat().st_size
                source_row = _loads(item["source_evidence_json"], {})
                width = int(source_row.get("width") or 0) or None
                height = int(source_row.get("height") or 0) or None
                asset = {
                    "id": asset_id, "file_token": "business-review-" + asset_id,
                    "filename": filename,
                    "source_path": "业务决策台/" + item["source_name"] + "/" + item["source_record_path"],
                    "brand_dir": item["brand"], "model_dir": item["model"], "format": ext.upper(),
                    "width": width, "height": height, "size_bytes": size,
                    "brand_id": "", "brand_name": item["brand"],
                    "model_id": item["gaia_model_id"], "model_name": item["model"],
                    "color": color, "standard_color": color, "match_level": "exact",
                    "match_method": "business_decision_desk", "match_status": "已匹配",
                    "candidates": "[]",
                    "note": "业务人员确认可用；" + actor_label + ("；" + note if note else ""),
                    "local_path": rel, "created_at": now,
                    "display_brand": item["brand"], "display_filename": filename,
                    "asset_type": categories.kind_label("white"), "display_color": color,
                    "quality_level": "A", "quality_reason": "业务人员确认可用",
                    "quality_source": "business_decision_desk", "quality_updated_at": now,
                    "rights_status": "unconfirmed", "usable_scope": "internal_only",
                    "category": "phone", "category_source": "business_review",
                    "source_channel": item["source_name"], "asset_kind": "white",
                    "model_source": "gaia", "sha256": item["image_sha256"],
                    "dup_group": item["image_sha256"][:16],
                    "verify_state": "human_approved", "disposition": "keep_white",
                }
                columns = list(asset)
                c.execute("INSERT INTO image_assets (" + ",".join(columns) + ") VALUES (" +
                          ",".join("?" for _ in columns) + ")",
                          [asset[k] for k in columns])
        c.execute("""UPDATE business_review_items SET status=?, selected_color=?, official_asset_id=?,
                     reviewer_id=?,reviewer_label=?,reviewer_note=?,decided_at=? WHERE item_id=?""",
                  (target, color if decision == "approve" else "", asset_id, actor_id,
                   actor_label, note, now, item_id))
        c.execute("""INSERT INTO business_review_events
            (item_id,action,from_status,to_status,selected_color,official_asset_id,
             reviewer_id,reviewer_label,reviewer_note,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (item_id, decision, current, target, color if decision == "approve" else "",
             asset_id, actor_id, actor_label, note, now))
        c.commit()
    return {"ok": True, "already_saved": False, "status": target, "asset_id": asset_id or None}


def import_report() -> dict:
    with closing(_conn()) as c:
        row = c.execute("""SELECT count(*),count(distinct image_sha256),
            sum(image_missing),sum(status='pending'),sum(status='approved'),
            sum(status='rejected'),sum(status='deferred')
            FROM business_review_items WHERE batch_id=?""", (BATCH_ID,)).fetchone()
    return dict(zip(("relations", "unique_images", "missing_images", "pending",
                     "approved", "rejected", "deferred"), row))
