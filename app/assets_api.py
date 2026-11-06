"""V0.3 图片资产库 API。

只读访问 image_assets 表；图片文件从本地资产目录读取。
- GET /api/v1/images            列表 + 筛选 + 分页（支持品牌组 group）
- GET /api/v1/images/{id}       详情
- GET /api/v1/images/{id}/file  图片内容（支持 ?w= 缩略图）
- GET /api/v1/images/{id}/download  附件下载
- GET /api/v1/images-facets     品牌组/品牌/颜色/状态聚合，随当前筛选上下文变化

V0.3.2 变更：品牌组（小米系 / vivo系 / OPPO系）只是**筛选聚合维度**，
不改写、不回填数据库里的图片原始品牌；颜色聚合始终基于全量匹配结果，
不依赖前端已加载的分页。
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

# ---------- 品牌组（仅筛选维度，不改写图片原始品牌） ----------
# 与 tools/gaia/matching.py 的 BRAND_ALIASES 保持同一套归一键，但此处独立维护：
# 容器内只挂载 app/，不能依赖仓库其他目录。
BRAND_ALIASES = {
    "apple": "apple", "苹果": "apple", "苹果笔记本": "apple",
    "iqoo": "iqoo",
    "oppo": "oppo",
    "vivo": "vivo",
    "redmi": "redmi", "红米": "redmi",
    "honor": "honor", "荣耀": "honor",
    "huawei": "huawei", "华为": "huawei",
    "xiaomi": "xiaomi", "小米": "xiaomi",
    "oneplus": "oneplus", "一加": "oneplus",
    "samsung": "samsung", "三星": "samsung",
    "realme": "realme", "真我": "realme",
    "nubia": "nubia", "努比亚": "nubia",
    "lenovo": "lenovo", "联想": "lenovo",
}

# 只登记多子品牌集团；华为、荣耀、三星、Apple、努比亚各自独立成品牌，不设组。
BRAND_GROUPS = (
    {"id": "xiaomi", "label": "小米系", "keys": ("xiaomi", "redmi")},
    {"id": "vivo", "label": "vivo系", "keys": ("vivo", "iqoo")},
    {"id": "oppo", "label": "OPPO系", "keys": ("oppo", "oneplus", "realme")},
)
# 展示字段：品牌取原始目录品牌优先，颜色取标准色优先（display_color 已按标准化规则清洗）
BRAND_EXPR = "coalesce(nullif(display_brand,''), brand_dir, brand_name, '')"
COLOR_EXPR = "coalesce(nullif(display_color,''), nullif(standard_color,''), '')"


def brand_variants(keys) -> tuple[str, ...]:
    """把一个品牌的全部已知写法展开成 SQL IN 列表（均为小写）。"""
    wanted = set(keys)
    variants = {k for k in wanted}
    variants |= {alias for alias, key in BRAND_ALIASES.items() if key in wanted}
    return tuple(sorted(variants))


def brand_key_of(value: str) -> str:
    """把展示品牌名归一到品牌键（Redmi→redmi、一加→oneplus）。"""
    key = (value or "").strip().casefold()
    return BRAND_ALIASES.get(key, key)


def build_where(q: str = "", model: str = "", status: str = "",
                group: str = "", brand: str = "", color: str = "") -> tuple[str, list]:
    """把筛选条件拼成 WHERE 子句；品牌组与子品牌同时给出时按 AND 处理（精确优先）。"""
    where: list[str] = []
    args: list = []

    if group:
        spec = next((g for g in BRAND_GROUPS if g["id"] == group), None)
        if spec is None:
            raise HTTPException(400, f"未知品牌组：{group}")
        variants = brand_variants(spec["keys"])
        where.append(f"lower({BRAND_EXPR}) in ({','.join('?' * len(variants))})")
        args += list(variants)
    if brand:
        where.append(f"{BRAND_EXPR} = ?")
        args.append(brand)
    if model:
        where.append("model_name LIKE ?")
        args.append(f"%{model}%")
    if color:
        where.append(f"{COLOR_EXPR} = ?")
        args.append(color)
    if status:
        where.append("match_status = ?")
        args.append(status)
    if q.strip():
        # 只搜品牌、型号、颜色；不搜原始文件名，避免文件名噪音污染结果
        kw = f"%{q.strip()}%"
        where.append(
            f"({BRAND_EXPR} LIKE ? OR model_name LIKE ? OR model_dir LIKE ? OR {COLOR_EXPR} LIKE ?)")
        args += [kw, kw, kw, kw]
    return ((" WHERE " + " AND ".join(where)) if where else ""), args



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
    d["display_color"] = d.get("display_color") or d.get("standard_color") or ""
    d["display_filename"] = d.get("display_filename") or d.get("filename") or ""
    d["asset_type"] = d.get("asset_type") or "图片"
    # 字段来源：standard=标准数据确认，folder/raw=目录或文件名原始值（未确认），none=缺失
    # 前端据此决定显示"标准型号"还是"目录型号（未标准化）"，避免把未识别值写成已确认。
    d["brand_source"] = "standard" if (d.get("brand_name") or "").strip() else (
        "folder" if (d.get("brand_dir") or "").strip() else "none")
    d["model_source"] = "standard" if (d.get("model_name") or "").strip() else (
        "folder" if (d.get("model_dir") or "").strip() else (
            "filename" if (d.get("filename") or "").strip() else "none"))
    d["color_source"] = "standard" if (d.get("standard_color") or "").strip() else (
        "raw" if (d.get("color") or "").strip() else "none")
    d["file_url"] = f"/api/v1/images/{d['id']}/file"
    d["thumb_url"] = f"/api/v1/images/{d['id']}/file?w=320"
    d["download_url"] = f"/api/v1/images/{d['id']}/download"
    return d


@router.get("/images")
def list_images(
    q: str = "",
    group: str = "",
    brand: str = "",
    model: str = "",
    color: str = "",
    status: str = "",
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    clause, args = build_where(q=q, model=model, status=status,
                               group=group, brand=brand, color=color)
    with db() as conn:
        total = conn.execute(f"select count(*) from image_assets{clause}", args).fetchone()[0]
        rows = conn.execute(
            f"""select * from image_assets{clause}
                order by brand_name, model_name, color
                limit ? offset ?""", args + [limit, offset]).fetchall()
    return {"total": total, "limit": limit, "offset": offset,
            "items": [row_to_dict(r) for r in rows]}


@router.get("/images-facets")
def facets(
    q: str = "",
    status: str = "",
    group: str = "",
    brand: str = "",
    color: str = "",
):
    """筛选聚合。

    每个维度的计数都基于当前筛选上下文的**全量**结果（SQL 聚合），
    与前端已加载的分页无关；同时遵守分面惯例——算某一维度时排除它自己，
    这样「切换品牌后颜色数量」才是切换后真实可选的资产数。
      - 品牌组：q + status
      - 品牌：  q + status + group
      - 颜色：  q + status + group + brand（+ 已选颜色不参与自身统计）
      - 状态：  q + group + brand + color
    """
    b_clause, b_args = build_where(q=q, status=status, group=group)


    c_clause, c_args = build_where(q=q, status=status, group=group, brand=brand)
    s_clause, s_args = build_where(q=q, group=group, brand=brand, color=color)

    with db() as conn:
        # 品牌组计数直接复用列表接口的同一套 WHERE，保证「组计数 == 点进去的总数」
        groups = []
        for spec in BRAND_GROUPS:
            clause, args = build_where(q=q, status=status, group=spec["id"])
            count = conn.execute(
                f"select count(*) from image_assets{clause}", args).fetchone()[0]
            groups.append({"id": spec["id"], "label": spec["label"], "count": count})

        brands = [{"value": r[0], "count": r[1]} for r in conn.execute(
            f"""select {BRAND_EXPR} v, count(*) c from image_assets{b_clause}
                {"and" if b_clause else "where"} {BRAND_EXPR} <> ''
                group by v order by c desc, v""", b_args)]

        colors = [{"value": r[0], "count": r[1], "standard": r[2]} for r in conn.execute(
            f"""select {COLOR_EXPR} v, count(*) c,
                       sum(case when coalesce(standard_color,'') <> '' then 1 else 0 end) s
                from image_assets{c_clause}
                {"and" if c_clause else "where"} {COLOR_EXPR} <> ''
                group by v order by c desc, v""", c_args)]

        statuses = [{"value": r[0], "count": r[1]} for r in conn.execute(
            f"""select match_status, count(*) c from image_assets{s_clause}
                group by match_status order by c desc""", s_args)]

        # 「全部颜色」这一项要显示当前品牌范围内的总数（含未标注颜色的图片）
        color_base = conn.execute(
            f"select count(*) from image_assets{c_clause}", c_args).fetchone()[0]

        stats = conn.execute(
            "select count(*), coalesce(sum(size_bytes),0) from image_assets").fetchone()

    return {"groups": groups, "brands": brands, "colors": colors, "statuses": statuses,
            "color_base": color_base,
            "total": stats[0], "total_bytes": stats[1]}


@router.get("/brand-tree")
def brand_tree(q: str = "", status: str = ""):
    """统一品牌选择器的数据源：品牌组（含子品牌）+ 独立品牌。

    V0.3.3 新增，纯新增接口，不影响 /images-facets 的既有契约。
    计数口径 = 搜索词 + 匹配状态，即「现在点它会有多少张」，全部走 SQL 聚合，与分页无关；
    同一品牌的多种写法（如 Redmi/REDMI）按归一键合并。

    刻意不按颜色收敛：颜色在换品牌时会被清空，若品牌列表也跟着颜色收缩，
    用户就看不到其他品牌了——这与「不懂分组也能找到任意品牌」的目标相反。
    """
    clause, args = build_where(q=q, status=status)
    with db() as conn:
        total = conn.execute(
            f"select count(*) from image_assets{clause}", args).fetchone()[0]
        rows = conn.execute(
            f"""select {BRAND_EXPR} v, lower({BRAND_EXPR}) k, count(*) c
                from image_assets{clause}
                {"and" if clause else "where"} {BRAND_EXPR} <> ''
                group by k, v""", args).fetchall()

    merged: dict[str, tuple[str, int]] = {}
    for value, key, count in rows:
        k = BRAND_ALIASES.get(key, key)
        name, n = merged.get(k, (value, 0))
        merged[k] = (value if count > n else name, n + count)

    groups = []
    for spec in BRAND_GROUPS:
        kids = [{"value": merged[k][0], "count": merged[k][1]}
                for k in spec["keys"] if k in merged]
        kids.sort(key=lambda x: (-x["count"], x["value"]))
        groups.append({"id": spec["id"], "label": spec["label"],
                       "count": sum(x["count"] for x in kids), "brands": kids})

    grouped = {k for spec in BRAND_GROUPS for k in spec["keys"]}
    standalone = sorted(({"value": v[0], "count": v[1]} for k, v in merged.items() if k not in grouped),
                        key=lambda x: (-x["count"], x["value"]))
    return {"total": total, "groups": groups, "standalone": standalone}


@router.get("/images-overview")
def overview():
    """首页指标：全部来自数据库实时统计，不写死。"""
    with db() as conn:
        total, bytes_total = conn.execute(
            "select count(*), coalesce(sum(size_bytes),0) from image_assets").fetchone()
        brands = conn.execute(
            f"select count(distinct {BRAND_EXPR}) from image_assets where {BRAND_EXPR} <> ''").fetchone()[0]
        models = conn.execute(
            "select count(distinct model_id) from image_assets where coalesce(model_id,'') <> ''").fetchone()[0]
        colors = conn.execute(
            f"select count(distinct {COLOR_EXPR}) from image_assets where {COLOR_EXPR} <> ''").fetchone()[0]
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
