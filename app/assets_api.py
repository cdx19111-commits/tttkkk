"""V0.3 图片资产库 API。

只读访问 image_assets 表；图片文件从本地资产目录读取。
- GET /api/v1/images            列表 + 筛选 + 分页
- GET /api/v1/images/{id}       详情
- GET /api/v1/images/{id}/file  图片内容（支持 ?w= 缩略图）
- GET /api/v1/images/{id}/download  附件下载
- GET /api/v1/images-facets     品牌/颜色聚合，供前端筛选
"""
from __future__ import annotations

import io
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

# 资产根目录由环境变量指定；容器内通常挂到 /data/v03。
# 不依赖代码所在层级，避免本地开发与容器内路径结构不同导致找不到文件。
_parents = Path(__file__).resolve().parents
DEFAULT_ROOT = (_parents[3] / "data" / "v03") if len(_parents) > 3 else Path("/data/v03")
ASSET_ROOT = Path(os.getenv("ASSET_ROOT", str(DEFAULT_ROOT)))
ASSET_DB = Path(os.getenv("ASSET_DB", str(ASSET_ROOT / "gallery.db")))
THUMB_DIR = Path(os.getenv("THUMB_DIR", str(ASSET_ROOT / "thumbs")))
try:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
except OSError:                      # 只读挂载时降级：不缓存缩略图，直接回原图
    THUMB_DIR = Path("/tmp/gallery-thumbs")
    THUMB_DIR.mkdir(parents=True, exist_ok=True)

MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp"}

router = APIRouter(prefix="/api/v1", tags=["images"])


def db() -> sqlite3.Connection:
    if not ASSET_DB.exists():
        raise HTTPException(503, "图片资产库尚未初始化")
    conn = sqlite3.connect(f"file:{ASSET_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d.pop("local_path", None)
    # 展示用字段：品牌取原始目录品牌；文件名用标准化名称
    d["display_brand"] = d.get("display_brand") or d.get("brand_dir") or d.get("brand_name") or ""
    d["display_color"] = d.get("standard_color") or d.get("color") or ""
    d["display_filename"] = d.get("display_filename") or d.get("filename") or ""
    d["asset_type"] = d.get("asset_type") or "图片"
    d["file_url"] = f"/api/v1/images/{d['id']}/file"
    d["thumb_url"] = f"/api/v1/images/{d['id']}/file?w=320"
    d["download_url"] = f"/api/v1/images/{d['id']}/download"
    return d


@router.get("/images")
def list_images(
    q: str = "",
    brand: str = "",
    model: str = "",
    color: str = "",
    status: str = "",
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    where, args = [], []
    if brand:
        # 按"展示品牌"筛选（原始目录品牌），避免跨界映射把一加算成小米
        where.append("coalesce(nullif(display_brand,''), brand_dir, brand_name) = ?")
        args.append(brand)
    if model:
        where.append("model_name LIKE ?"); args.append(f"%{model}%")
    if color:
        where.append("coalesce(nullif(standard_color,''), color) = ?"); args.append(color)
    if status:
        where.append("match_status = ?"); args.append(status)
    if q.strip():
        # 只搜品牌、型号、颜色；不搜原始文件名，避免文件名噪音污染结果
        kw = f"%{q.strip()}%"
        where.append(
            "(coalesce(nullif(display_brand,''), brand_dir) LIKE ? "
            " OR model_name LIKE ? OR model_dir LIKE ? "
            " OR coalesce(nullif(standard_color,''), color) LIKE ?)")
        args += [kw, kw, kw, kw]
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    with db() as conn:
        total = conn.execute(f"select count(*) from image_assets{clause}", args).fetchone()[0]
        rows = conn.execute(
            f"""select * from image_assets{clause}
                order by brand_name, model_name, color
                limit ? offset ?""", args + [limit, offset]).fetchall()
    return {"total": total, "limit": limit, "offset": offset,
            "items": [row_to_dict(r) for r in rows]}


@router.get("/images-facets")
def facets():
    with db() as conn:
        brands = [{"value": r[0], "count": r[1]} for r in conn.execute(
            "select coalesce(nullif(display_brand,''), brand_dir) b, count(*) c from image_assets "
            "where b<>'' group by b order by c desc")]
        colors = [{"value": r[0], "count": r[1]} for r in conn.execute(
            "select coalesce(nullif(standard_color,''), color) v, count(*) c from image_assets "
            "where v<>'' group by v order by c desc limit 60")]
        statuses = [{"value": r[0], "count": r[1]} for r in conn.execute(
            "select match_status, count(*) c from image_assets group by match_status")]
        stats = conn.execute(
            "select count(*), coalesce(sum(size_bytes),0) from image_assets").fetchone()
    return {"brands": brands, "colors": colors, "statuses": statuses,
            "total": stats[0], "total_bytes": stats[1]}


@router.get("/images-overview")
def overview():
    """首页指标：全部来自数据库实时统计，不写死。"""
    with db() as conn:
        total, bytes_total = conn.execute(
            "select count(*), coalesce(sum(size_bytes),0) from image_assets").fetchone()
        brands = conn.execute(
            "select count(distinct coalesce(nullif(display_brand,''), brand_dir)) "
            "from image_assets where coalesce(nullif(display_brand,''), brand_dir, '') <> ''").fetchone()[0]
        models = conn.execute(
            "select count(distinct model_id) from image_assets where coalesce(model_id,'') <> ''").fetchone()[0]
        colors = conn.execute(
            "select count(distinct coalesce(nullif(standard_color,''), color)) "
            "from image_assets where coalesce(standard_color, color, '') <> ''").fetchone()[0]
        by_status = {r[0]: r[1] for r in conn.execute(
            "select match_status, count(*) from image_assets group by match_status")}
    return {
        "total": total,
        "total_bytes": bytes_total,
        "brands": brands,
        "models": models,
        "colors": colors,
        "matched": by_status.get("已匹配", 0),
        "pending": by_status.get("待处理", 0),
        "unmatched": by_status.get("未匹配", 0),
        "by_status": by_status,
    }


@router.get("/images/{image_id}")
def get_image(image_id: str):
    with db() as conn:
        r = conn.execute("select * from image_assets where id = ?", (image_id,)).fetchone()
    if not r:
        raise HTTPException(404, "图片不存在")
    d = row_to_dict(r)
    d["path_parts"] = d["source_path"].split("/")
    return d


def _file_row(image_id: str) -> sqlite3.Row:
    with db() as conn:
        r = conn.execute("select * from image_assets where id = ?", (image_id,)).fetchone()
    if not r:
        raise HTTPException(404, "图片不存在")
    return r


@router.get("/images/{image_id}/file")
def image_file(image_id: str, w: int = Query(default=0, ge=0, le=1600)):
    r = _file_row(image_id)
    local = r["local_path"] or ""
    src = (ASSET_ROOT / local) if local else None
    if not src or not src.is_file():
        raise HTTPException(404, "图片文件缺失")

    mime = MIME.get((r["format"] or "").lower(), "application/octet-stream")

    if w <= 0:
        return FileResponse(src, media_type=mime,
                            headers={"Cache-Control": "private, max-age=3600"})

    cached = THUMB_DIR / f"{image_id}_w{w}.webp"
    if not cached.is_file():
        try:
            from PIL import Image
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((w, w))
                im.save(cached, "WEBP", quality=82, method=4)
        except Exception:
            return FileResponse(src, media_type=mime)
    return FileResponse(cached, media_type="image/webp",
                        headers={"Cache-Control": "private, max-age=86400"})


@router.get("/images/{image_id}/download")
def download(image_id: str):
    r = _file_row(image_id)
    local = r["local_path"] or ""
    src = (ASSET_ROOT / local) if local else None
    if not src or not src.is_file():
        raise HTTPException(404, "图片文件缺失")
    mime = MIME.get((r["format"] or "").lower(), "application/octet-stream")
    # 规范文件名优先；缺失时回退原始文件名
    name = r["display_filename"] or r["filename"]
    return FileResponse(
        src, media_type=mime, filename=name,
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(name)}"},
    )
