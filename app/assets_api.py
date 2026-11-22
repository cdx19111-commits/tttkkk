"""统一图库 API（v1.2-final 基线 + V2.1 多品类合入）。

只读访问 image_assets 表；图片文件从本地资产目录读取。

V2.1 合入增量（手机线 v1.2 语义原样保留）：
- category（缺省=phone）/ asset_kind（非手机默认仅白底）
- publish_state 治理（candidate/restricted；冻结素材仅限管理通道）
- /catalog 品类字典；质量分级语义仅对手机行生效
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
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from app import categories

# 资产根目录由环境变量指定；容器内通常挂到 /data/v03。
# 不依赖代码所在层级，避免本地开发与容器内路径结构不同导致找不到文件。
_parents = Path(__file__).resolve().parents
DEFAULT_ROOT = (_parents[3] / "data" / "v03") if len(_parents) > 3 else Path("/data/v03")
ASSET_ROOT = Path(os.getenv("ASSET_ROOT", str(DEFAULT_ROOT)))
ASSET_DB = Path(os.getenv("ASSET_DB", str(ASSET_ROOT / "gallery.db")))
THUMB_DIR = Path(os.getenv("THUMB_DIR", str(ASSET_ROOT / "thumbs")))
MODEL_PARAMS_PATH = Path(__file__).resolve().parent / "static" / "model_params.json"
_MODEL_PARAMS_CACHE: dict | None = None
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

# ---------------------------------------------------------------------------
# V1.7 双配色（仅非手机）
# ---------------------------------------------------------------------------
# 手机：口径一律不变（display_color → standard_color），2,092 张行为零改动。
PHONE_COLOR_EXPR = "coalesce(nullif(display_color,''), nullif(standard_color,''), '')"
# 非手机：业务口径 = 盖亚参考色。缺失即空，**绝不**回落到官方色/历史标准色，
# 避免把「品牌官方配色」伪装成「盖亚参考色」。
GAIA_COLOR_EXPR = "coalesce(nullif(gaia_color,''), '')"
OFFICIAL_COLOR_EXPR = "coalesce(nullif(official_color,''), '')"
# 统一筛选/排序表达式：按品类分流，手机走旧口径，非手机走盖亚口径。
COLOR_EXPR = (f"(case when coalesce(category,'phone') = 'phone' "
              f"then {PHONE_COLOR_EXPR} else {GAIA_COLOR_EXPR} end)")


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


def role_of(request: Request) -> str:
    """会话角色只来自服务端 Session，不接受查询参数。"""
    return getattr(request.state, "role", "business") or "business"


def can_review(request: Request) -> bool:
    return role_of(request) == "review"


def _cat(value: str) -> str:
    """品类参数归一：空/缺省 = phone；'all' 原样；未知值回落 phone。"""
    v = (value or "").strip().lower()
    if not v:
        return "phone"
    if v == "all" or categories.get(v):
        return v
    return "phone"


def build_where(q: str = "", model: str = "", status: str = "",
                group: str = "", brand: str = "", color: str = "",
                official_color: str = "",
                quality_only: bool = True,
                category: str = "phone", asset_kind: str = "",
                publish_state: str = "",
                model_exact: str = "", model_source: str = "") -> tuple[str, list]:
    """把筛选条件拼成 WHERE 子句。

    V2.1 语义：category 缺省 = phone（手机业务视图与 v1.2 完全一致，含质量分级）；
    非手机类目默认仅白底且排除 restricted；质量分级仅对手机行生效；
    显式 publish_state=restricted → 403（冻结素材仅限管理通道）。

    V0.3.7 新增：model_exact + model_source 精确筛选型号。
    - standard：已确认标准型号，匹配 model_name；
    - directory：未标准化的目录型号，仅在 model_name 为空时匹配 model_dir。
      两者互斥，保证同一张图不会被两个来源重复计入；同名的标准型号与目录型号
      也会被严格区分，不会因为名字相同而混在一起。
    """
    where: list[str] = []
    args: list = []

    cat = _cat(category)
    if cat != "all":
        where.append("coalesce(category, 'phone') = ?")
        args.append(cat)
    if asset_kind:
        where.append("asset_kind = ?")
        args.append(asset_kind)
    elif cat != "phone":
        where.append("asset_kind = 'white'")
    if cat != "phone":
        if publish_state:
            if publish_state == "restricted":
                raise HTTPException(403, "冻结素材仅限管理通道（数据库/运维脚本）检索")
            where.append("coalesce(publish_state, 'published') = ?")
            args.append(publish_state)
        else:
            where.append("coalesce(publish_state, '') <> 'restricted'")

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
    if model_exact:
        # 型号精确筛选（V0.3.7）：标准型号与目录型号严格分流，互不重叠
        src = (model_source or "standard").strip().lower()
        if src == "standard":
            where.append("coalesce(model_name,'') = ?")
            args.append(model_exact)
        elif src == "directory":
            where.append("coalesce(model_name,'') = '' and coalesce(model_dir,'') = ?")
            args.append(model_exact)
        else:
            raise HTTPException(400, f"未知型号来源：{model_source}")
    if color:
        # 非手机按盖亚参考色筛选；手机仍按 display_color/standard_color 旧口径
        where.append(f"{COLOR_EXPR} = ?")
        args.append(color)
    if official_color:
        # 官方配色检索：只对非手机生效，手机行没有该字段，返回空集而不是混入手机数据
        where.append("coalesce(category,'phone') <> 'phone'")
        where.append(f"{OFFICIAL_COLOR_EXPR} = ?")
        args.append(official_color)
    if status:
        where.append("match_status = ?")
        args.append(status)
    if quality_only:
        # 业务视图只认 A 级；复核视图由调用方传 False。
        # V2.1：该语义仅对手机行生效；非手机行有自己的 publish_state 治理。
        if cat == "phone":
            where.append("coalesce(quality_level,'B') = 'A'")
        elif cat == "all":
            where.append("(coalesce(category,'phone') <> 'phone' or coalesce(quality_level,'B') = 'A')")
    if q.strip():
        # 只搜品牌、型号、颜色；不搜原始文件名，避免文件名噪音污染结果。
        # V1.7：非手机的盖亚参考色与品牌官方配色都参与检索（官方名如「月神灰」可搜到）。
        kw = f"%{q.strip()}%"
        where.append(
            f"({BRAND_EXPR} LIKE ? OR model_name LIKE ? OR model_dir LIKE ?"
            f" OR {PHONE_COLOR_EXPR} LIKE ?"
            f" OR {GAIA_COLOR_EXPR} LIKE ? OR {OFFICIAL_COLOR_EXPR} LIKE ?)")
        args += [kw, kw, kw, kw, kw, kw]
    return ((" WHERE " + " AND ".join(where)) if where else ""), args



def db() -> sqlite3.Connection:
    if not ASSET_DB.exists():
        raise HTTPException(503, "图片资产库尚未初始化")
    conn = sqlite3.connect(f"file:{ASSET_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# fail-closed 守卫（V1.0 发布加固）
# 目的：如果服务被启动在一个**尚未完成质量分层迁移**的库上（例如迁移失败后
# 恢复了旧备份、或新镜像被误指向旧库），必须整体拒绝服务，而不是把 2,092 张
# 图连 B/X 一起放出去。宁可 503，不做"看起来能用但会出错图"的降级。
# 结果只在进程内缓存一次；容器重建即重新判断。
# ---------------------------------------------------------------------------
_SCHEMA_STATE: dict[str, object] = {}


def quality_schema_ok() -> tuple[bool, str]:
    if _SCHEMA_STATE:
        return bool(_SCHEMA_STATE.get("ok")), str(_SCHEMA_STATE.get("msg", ""))
    try:
        if not ASSET_DB.exists():
            _SCHEMA_STATE.update(ok=False, msg="数据库文件不存在")
            return False, "数据库文件不存在"
        conn = sqlite3.connect(f"file:{ASSET_DB}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(image_assets)")}
        finally:
            conn.close()
        if "quality_level" not in cols:
            _SCHEMA_STATE.update(ok=False, msg="缺少 quality_level 列（未完成质量分层迁移）")
            return False, "缺少 quality_level 列（未完成质量分层迁移）"
        _SCHEMA_STATE.update(ok=True, msg="ok")
        return True, "ok"
    except Exception as exc:  # 任何异常一律视为不安全
        _SCHEMA_STATE.update(ok=False, msg=f"数据库检查失败：{type(exc).__name__}")
        return False, f"数据库检查失败：{type(exc).__name__}"


def reset_schema_cache() -> None:
    _SCHEMA_STATE.clear()


def access_of(state: str | None) -> dict:
    """非手机发布态 → 访问能力。NULL=沿用现状（不冒充已审批）；candidate 仅预览。"""
    if not state:
        return {"preview": True, "original": True, "download": True, "legacy": True}
    return {"preview": state in ("published", "candidate"),
            "original": state == "published",
            "download": state == "published",
            "legacy": False}


def row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d.pop("local_path", None)
    # 展示用字段：品牌取原始目录品牌；文件名用标准化名称
    d["display_brand"] = d.get("display_brand") or d.get("brand_dir") or d.get("brand_name") or ""
    d["display_color"] = d.get("display_color") or d.get("standard_color") or ""
    d["display_filename"] = d.get("display_filename") or d.get("filename") or ""
    # asset_kind 是唯一用途 ID；asset_type 仅作为兼容展示字段，由 ID 派生。
    d["asset_kind"] = (d.get("asset_kind") or "").strip()
    d["asset_kind_label"] = categories.kind_label(d["asset_kind"])
    d["asset_type"] = d["asset_kind_label"]
    # 字段来源：standard=标准数据确认，folder/raw=目录或文件名原始值（未确认），none=缺失
    # 前端据此决定显示"标准型号"还是"目录型号（未标准化）"，避免把未识别值写成已确认。
    d["brand_source"] = "standard" if (d.get("brand_name") or "").strip() else (
        "folder" if (d.get("brand_dir") or "").strip() else "none")
    d["model_source"] = "standard" if (d.get("model_name") or "").strip() else (
        "folder" if (d.get("model_dir") or "").strip() else (
            "filename" if (d.get("filename") or "").strip() else "none"))
    d["color_source"] = "standard" if (d.get("standard_color") or "").strip() else (
        "raw" if (d.get("color") or "").strip() else "none")
    d["quality_level"] = d.get("quality_level") or "B"
    d["rights_status"] = d.get("rights_status") or "unconfirmed"
    d["usable_scope"] = d.get("usable_scope") or "internal_only"
    if d["quality_level"] == "A":
        d.pop("quality_reason", None)
    # V2.1 多品类维度（手机行保持旧展示）
    d["category"] = d.get("category") or "phone"
    d["category_label"] = categories.label(d["category"]) if d["category"] != "phone" else "手机"
    if d["category"] != "phone":
        d["publish_state"] = d.get("publish_state")
        d["publish_label"] = "沿用现状" if not d["publish_state"] else categories.publish_label(d["publish_state"])
        d["publish_is_legacy"] = not d["publish_state"]
        acc = access_of(d["publish_state"])
        d["can_view_original"] = acc["original"]
        d["can_download"] = acc["download"]
        # V1.7 双配色：非手机的展示色 = 盖亚参考色；官方配色单独给出，互不顶替。
        # display_color 的历史值（含文件名噪音与历史官方色）不再作为非手机展示口径。
        d["gaia_color"] = (d.get("gaia_color") or "").strip()
        d["official_color"] = (d.get("official_color") or "").strip()
        d["display_color"] = d["gaia_color"]
        d["official_display_color"] = d["official_color"]
        d["color_source"] = "gaia" if d["gaia_color"] else (
            "official" if d["official_color"] else "none")
        d["color_mode"] = "dual"
    else:
        d["can_view_original"] = True
        d["can_download"] = True
        # 手机：保持单一颜色口径，新增字段留空，前端不展示第二色
        d["gaia_color"] = ""
        d["official_color"] = ""
        d["official_display_color"] = ""
        d["color_mode"] = "single"
    d["file_url"] = f"/api/v1/images/{d['id']}/file"
    d["thumb_url"] = f"/api/v1/images/{d['id']}/file?w=320"
    d["download_url"] = f"/api/v1/images/{d['id']}/download"
    return d


@router.get("/images")
def list_images(
    request: Request,
    q: str = "",
    group: str = "",
    brand: str = "",
    model: str = "",
    model_exact: str = "",
    model_source: str = "",
    color: str = "",
    official_color: str = "",
    status: str = "",
    view: str = "",
    category: str = "phone",
    asset_kind: str = "",
    publish_state: str = "",
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    review = can_review(request) and view == "review"
    clause, args = build_where(q=q, model=model, status=status, group=group,
                               brand=brand, color=color, official_color=official_color,
                               quality_only=not review,
                               category=category, asset_kind=asset_kind,
                               publish_state=publish_state,
                               model_exact=model_exact, model_source=model_source)
    with db() as conn:
        total = conn.execute(f"select count(*) from image_assets{clause}", args).fetchone()[0]
        rows = conn.execute(
            f"""select * from image_assets{clause}
                order by brand_name, model_name,
                         case when coalesce(category,'phone') <> 'phone'
                                   and coalesce(gaia_color,'') = '' then 1 else 0 end,
                         case when coalesce(category,'phone') = 'phone'
                              then coalesce(color,'') else {GAIA_COLOR_EXPR} end,
                         filename
                limit ? offset ?""", args + [limit, offset]).fetchall()
        suppressed = 0
        if not review:
            hide_clause, hide_args = build_where(q=q, model=model, status=status, group=group,
                                                 brand=brand, color=color,
                                                 official_color=official_color,
                                                 quality_only=False,
                                                 category=category, asset_kind=asset_kind,
                                                 model_exact=model_exact, model_source=model_source)
            all_n = conn.execute(f"select count(*) from image_assets{hide_clause}",
                                 hide_args).fetchone()[0]
            suppressed = all_n - total
    payload = {"total": total, "limit": limit, "offset": offset,
               "items": [row_to_dict(r) for r in rows],
               "view": "review" if review else "business",
               "suppressed": suppressed, "empty_hint": ""}
    if total == 0 and model:
        with db() as conn:
            cov = _coverage(conn, model)
        if cov and cov["pending"] + cov["deactivated"] > 0:
            payload["empty_hint"] = (
                f"该型号暂无已确认图片：另有 {cov['pending']} 张待确认、"
                f"{cov['deactivated']} 张已停用，需复核后才会出现在业务视图。")
        elif cov:
            payload["empty_hint"] = "该型号在图库中暂无图片记录。"
    elif total == 0 and suppressed:
        payload["empty_hint"] = f"当前条件下没有已确认图片（已隐藏 {suppressed} 张待确认/已停用素材）。"
    return payload


@router.get("/images-facets")
def facets(
    request: Request,
    q: str = "",
    status: str = "",
    group: str = "",
    brand: str = "",
    color: str = "",
    official_color: str = "",
    view: str = "",
    category: str = "phone",
    asset_kind: str = "",
    model_exact: str = "",
    model_source: str = "",
):
    review = can_review(request) and view == "review"
    qo = not review
    """筛选聚合。

    每个维度的计数都基于当前筛选上下文的**全量**结果（SQL 聚合），
    与前端已加载的分页无关；同时遵守分面惯例——算某一维度时排除它自己，
    这样「切换品牌后颜色数量」才是切换后真实可选的资产数。
      - 品牌组：q + status
      - 品牌：  q + status + group
      - 型号：  q + status + group + brand（不含颜色，避免选色后型号列表塌缩）
      - 颜色：  q + status + group + brand + 型号（+ 已选颜色不参与自身统计）
      - 状态：  q + group + brand + color + 型号
    """
    kw = dict(q=q, status=status, quality_only=qo, category=category, asset_kind=asset_kind)
    b_clause, b_args = build_where(group=group, **kw)
    m_clause, m_args = build_where(group=group, brand=brand, **kw)
    c_clause, c_args = build_where(group=group, brand=brand, official_color=official_color,
                                   model_exact=model_exact, model_source=model_source, **kw)
    # 官方配色分面：统计时排除官方色自身（与颜色分面同一惯例）
    o_clause, o_args = build_where(group=group, brand=brand, color=color,
                                   model_exact=model_exact, model_source=model_source, **kw)
    s_clause, s_args = build_where(group=group, brand=brand, color=color,
                                   official_color=official_color,
                                   model_exact=model_exact, model_source=model_source, **kw)

    with db() as conn:
        # 品牌组计数直接复用列表接口的同一套 WHERE，保证「组计数 == 点进去的总数」
        groups = []
        for spec in BRAND_GROUPS:
            clause, args = build_where(q=q, status=status, group=spec["id"], quality_only=qo,
                                       category=category, asset_kind=asset_kind)
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

        # V1.7 官方配色分面（仅非手机）：品牌正式命名，供展示与检索，不参与「盖亚色」筛选口径
        official_colors = [{"value": r[0], "count": r[1]} for r in conn.execute(
            f"""select {OFFICIAL_COLOR_EXPR} v, count(*) c from image_assets{o_clause}
                {"and" if o_clause else "where"} coalesce(category,'phone') <> 'phone'
                and {OFFICIAL_COLOR_EXPR} <> ''
                group by v order by c desc, v""", o_args)]

        statuses = [{"value": r[0], "count": r[1]} for r in conn.execute(
            f"""select match_status, count(*) c from image_assets{s_clause}
                group by match_status order by c desc""", s_args)]

        # 「全部颜色」这一项要显示当前品牌范围内的总数（含未标注颜色的图片）
        color_base = conn.execute(
            f"select count(*) from image_assets{c_clause}", c_args).fetchone()[0]

        # 型号选项（V0.3.7）：标准型号与目录型号分开统计，互不重叠。
        # 统计口径 = 品类 + 搜索词 + 状态 + 品牌组/品牌（不受颜色与已选型号影响），
        # 全部走 SQL 聚合，与当前分页无关，所以「选项数字 == 点进去的张数」。
        glue = "and" if m_clause else "where"
        std_models = [{"value": r[0], "count": r[1]} for r in conn.execute(
            f"""select model_name v, count(*) c from image_assets{m_clause}
                {glue} coalesce(model_name,'') <> ''
                group by v order by c desc, v""", m_args)]
        dir_models = [{"value": r[0], "count": r[1]} for r in conn.execute(
            f"""select model_dir v, count(*) c from image_assets{m_clause}
                {glue} coalesce(model_name,'') = '' and coalesce(model_dir,'') <> ''
                group by v order by c desc, v""", m_args)]
        model_base = conn.execute(
            f"select count(*) from image_assets{m_clause}", m_args).fetchone()[0]

        if _cat(category) == "phone":
            stats_clause, stats_args = (" where coalesce(category,'phone')='phone' and "
                                        + ("quality_level='A'" if qo else "1=1")), []
        else:
            stats_clause, stats_args = build_where(category=category, asset_kind=asset_kind,
                                                   quality_only=qo)
        stats = conn.execute(
            "select count(*), coalesce(sum(size_bytes),0) from image_assets" + stats_clause,
            stats_args).fetchone()

    return {"groups": groups, "brands": brands, "colors": colors, "statuses": statuses,
            "official_colors": official_colors,
            "color_mode": "single" if _cat(category) == "phone" else "dual",
            "color_base": color_base,
            "models": {"standard": std_models, "directory": dir_models,
                       "total": len(std_models) + len(dir_models), "base": model_base},
            "model_base": model_base,
            "total": stats[0], "total_bytes": stats[1]}


@router.get("/brand-tree")
def brand_tree(request: Request, q: str = "", status: str = "", view: str = "",
               category: str = "phone"):
    qo = not (can_review(request) and view == "review")
    """统一品牌选择器的数据源：品牌组（含子品牌）+ 独立品牌。

    V0.3.3 新增，纯新增接口，不影响 /images-facets 的既有契约。
    计数口径 = 搜索词 + 匹配状态，即「现在点它会有多少张」，全部走 SQL 聚合，与分页无关；
    同一品牌的多种写法（如 Redmi/REDMI）按归一键合并。

    刻意不按颜色收敛：颜色在换品牌时会被清空，若品牌列表也跟着颜色收缩，
    用户就看不到其他品牌了——这与「不懂分组也能找到任意品牌」的目标相反。
    """
    clause, args = build_where(q=q, status=status, quality_only=qo, category=category)
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
def overview(request: Request, category: str = "phone"):
    """首页指标：全部来自数据库实时统计，不写死。

    手机（缺省）：业务视图只统计 A 级（v1.2 语义原样，覆盖率计算同 v1.2）；
    非手机类目：白底 + 排除 restricted；all：手机 A 级 + 非手机白底。
    """
    review = can_review(request)
    cat = _cat(category)
    if cat == "phone":
        scope = "" if review else " and coalesce(quality_level,'B')='A'"
        base = " where coalesce(category,'phone')='phone' and asset_kind='white'" + scope
        base_args: list = []
    else:
        # 业务视图（未 review）也要遵守手机 A 级语义：quality_only = not review
        wclause, wargs = build_where(category=category, quality_only=not review)
        base = wclause or " where 1=1"
        base_args: list = wargs
    with db() as conn:
        total, bytes_total = conn.execute(
            f"select count(*), coalesce(sum(size_bytes),0) from image_assets{base}", base_args).fetchone()
        brands = conn.execute(
            f"select count(distinct {BRAND_EXPR}) from image_assets{base} and {BRAND_EXPR} <> ''",
            base_args).fetchone()[0]
        models = conn.execute(
            f"select count(distinct model_id) from image_assets{base} and coalesce(model_id,'') <> ''",
            base_args).fetchone()[0]
        colors = conn.execute(
            f"select count(distinct {COLOR_EXPR}) from image_assets{base} and {COLOR_EXPR} <> ''",
            base_args).fetchone()[0]
        by_status = {r[0]: r[1] for r in conn.execute(
            f"select match_status, count(*) from image_assets{base} group by match_status", base_args)}
        # 型号×颜色可信覆盖率：全部组合（model_id+standard_color 双非空）中已有 A 级图的占比，实时计算
        combos_all, combos_a = conn.execute(
            """select count(*),
                      sum(case when a_n > 0 then 1 else 0 end)
               from (select sum(case when quality_level='A' then 1 else 0 end) a_n
                     from image_assets where asset_kind='white' and model_id<>'' and standard_color<>'' group by model_id, standard_color)"""
        ).fetchone()
    coverage_ratio = f"{combos_a / combos_all * 100:.1f}%" if combos_all else "—"
    by_category = {r[0]: r[1] for r in conn.execute(
        """select coalesce(category,'phone') c, count(*) n from image_assets
            where coalesce(publish_state,'') <> 'restricted'
              and asset_kind = 'white'
              and (coalesce(category,'phone') = 'phone' and coalesce(quality_level,'B')='A'
                   or coalesce(category,'phone') <> 'phone')
            group by c""")}
    return {
        "total": total,
        "total_bytes": bytes_total,
        "brands": brands,
        "models": models,
        "colors": colors,
        "matched": by_status.get("已匹配", 0),
        "unmatched": by_status.get("未匹配", 0),
        "coverage_combos": combos_a,
        "coverage_total": combos_all,
        "coverage_ratio": coverage_ratio,
        "by_status": by_status,
        "by_category": by_category,
    }


def _visible_row(image_id: str, request: Request) -> sqlite3.Row:
    """统一的可见性判定。

    手机行：业务角色只能拿到 A 级，其余按不存在处理（不泄露存在性）——v1.2 原样。
    非手机行：publish_state 治理——restricted 按不存在处理；candidate/published 可见。
    """
    with db() as conn:
        r = conn.execute("select * from image_assets where id = ?", (image_id,)).fetchone()
    if not r:
        raise HTTPException(404, "图片不存在")
    cat = r["category"] or "phone"
    if cat == "phone":
        if not can_review(request) and (r["quality_level"] or "B") != "A":
            raise HTTPException(404, "图片不存在")
        return r
    if (r["publish_state"] or "") == "restricted":
        raise HTTPException(404, "图片不存在")
    return r


def _coverage(conn: sqlite3.Connection, model: str) -> dict | None:
    """按型号给出可信/待确认/已停用计数；用于「暂无已确认图片」提示。"""
    row = conn.execute(
        """select
             sum(case when quality_level='A' then 1 else 0 end) a,
             sum(case when quality_level='B' then 1 else 0 end) b,
             sum(case when quality_level='X' then 1 else 0 end) x,
             count(*) n, max(model_name) nm, max(model_id) mid
           from image_assets where model_name like ? or model_id = ?""",
        (f"%{model}%", model)).fetchone()
    if not row or not row["n"]:
        return None
    return {"model_id": row["mid"] or model, "model_name": row["nm"] or model,
            "confirmed": row["a"] or 0, "pending": row["b"] or 0,
            "deactivated": row["x"] or 0, "total": row["n"],
            "has_confirmed": bool(row["a"])}


@router.get("/me")
def me(request: Request):
    role = role_of(request)
    return {"role": role, "can_review": role == "review"}


@router.get("/coverage/{model_id}")
def coverage(model_id: str, request: Request):
    with db() as conn:
        cov = _coverage(conn, model_id)
    if not cov:
        raise HTTPException(404, "该型号在图库中没有记录")
    return cov


@router.get("/catalog")
def catalog(request: Request):
    """品类/素材类型字典（前端下拉数据源）。

    计数口径 = 默认检索口径：手机 A 级 + 非手机白底，均排除冻结素材。
    """
    with db() as conn:
        cats = {r[0]: r[1] for r in conn.execute(
            """select coalesce(category,'phone') c, count(*) n from image_assets
               where coalesce(publish_state,'') <> 'restricted'
                 and asset_kind = 'white'
                 and (coalesce(category,'phone') = 'phone' and coalesce(quality_level,'B')='A'
                      or coalesce(category,'phone') <> 'phone')
               group by c""")}
    data = categories.public_catalog()
    for c in data["categories"]:
        c["count"] = cats.get(c["id"], 0)
    data["total"] = sum(cats.values())
    return data


@router.get("/images/{image_id}")
def get_image(image_id: str, request: Request):
    r = _visible_row(image_id, request)
    d = row_to_dict(r)
    d["path_parts"] = d["source_path"].split("/")
    d["model_params"] = (model_params_for(d.get("model_id", ""))
                         if d.get("category", "phone") == "phone" else [])
    d["model_params_count"] = len(d["model_params"])
    return d


def model_params_for(model_id: str) -> list[dict[str, str]]:
    """Return ordered parameter name/value pairs for a Gaia model ID."""
    global _MODEL_PARAMS_CACHE
    if _MODEL_PARAMS_CACHE is None:
        try:
            payload = json.loads(MODEL_PARAMS_PATH.read_text(encoding="utf-8"))
            models = payload["models"]
            if not isinstance(models, dict):
                raise ValueError("models must be an object")
            _MODEL_PARAMS_CACHE = models
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(503, "型号参数数据暂不可用") from exc

    entry = _MODEL_PARAMS_CACHE.get(str(model_id or ""), {})
    raw_params = entry.get("params", []) if isinstance(entry, dict) else []
    if not isinstance(raw_params, list):
        raise HTTPException(503, "型号参数数据格式无效")
    return [
        {"name": str(pair[0]), "value": str(pair[1])}
        for pair in raw_params
        if isinstance(pair, (list, tuple)) and len(pair) >= 2
        and pair[0] not in (None, "") and pair[1] not in (None, "")
    ]


def _file_row(image_id: str, request: Request) -> sqlite3.Row:
    return _visible_row(image_id, request)


@router.get("/images/{image_id}/file")
def image_file(image_id: str, request: Request, w: int = Query(default=0, ge=0, le=1600)):
    r = _file_row(image_id, request)
    local = r["local_path"] or ""
    src = (ASSET_ROOT / local) if local else None
    if not src or not src.is_file():
        raise HTTPException(404, "图片文件缺失")

    mime = MIME.get((r["format"] or "").lower(), "application/octet-stream")

    if w <= 0:
        # V2.1：非手机候选素材只提供重编码预览，原图字节不出库
        if (r["category"] or "phone") != "phone" and not access_of(r["publish_state"])["original"]:
            raise HTTPException(403, "候选素材仅提供缩略预览，不提供原图")
        # no-cache：允许浏览器存副本，但每次必须回源校验——退出登录后不会再从本地缓存拿到图片
        return FileResponse(src, media_type=mime,
                            headers={"Cache-Control": "private, no-cache"})

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
                        headers={"Cache-Control": "private, no-cache"})


@router.get("/images/{image_id}/download")
def download(image_id: str, request: Request):
    r = _file_row(image_id, request)
    # V2.1：非手机行只有 published 可下载（candidate/restricted 均 403）
    if (r["category"] or "phone") != "phone" and not access_of(r["publish_state"])["download"]:
        raise HTTPException(403, f"该素材状态为「{categories.publish_label(r['publish_state'])}」，不允许下载")
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
