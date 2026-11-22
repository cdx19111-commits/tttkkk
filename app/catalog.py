"""V2.0 目录解析：把飞书路径翻译成「品类 / 素材类型 / 品牌 / 型号 / 颜色」。

只依赖 `categories.py` 的配置，不出现任何具体品类常量——
新增手表、笔记本时，只要 `categories.py` 里有定义，这里就能解析。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from . import categories

IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif", "bmp"}
DETAIL_DIRS = {"商详", "详情", "详情页", "长图", "商品详情"}
# 渠道/用途词：出现在文件名里说明是"给渠道用的图"，不是颜色
USAGE_WORDS = ("抖音商城", "玲珑", "主图", "白底", "商详", "详情")
# 英文色名白名单：除中文字色名外，只有这些拉丁词才算颜色
LATIN_COLORS = ("black", "white", "silver", "gray", "grey", "blue", "green", "red",
                "gold", "purple", "pink", "orange", "yellow", "brown", "titanium",
                "graphite", "midnight", "starlight", "space", "natural", "desert")

# 三类来源：一级目录 → 素材类型语义
SOURCE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "feishu_white",
        "label": "飞书白底图库",
        "root": "白底图库",
        "asset_kind": "white",
        "has_shop": False,
    },
    {
        "id": "douyin",
        "label": "抖音主图",
        "root": "抖音主图",
        "asset_kind": "marketing",
        "has_shop": True,
    },
    {
        "id": "jd",
        "label": "京东主图",
        "root": "京东主图",
        "asset_kind": "marketing",
        "has_shop": False,
    },
)

_SOURCE_BY_ROOT = {s["root"]: s for s in SOURCE_SPECS}

# 文件名里的重复序号：蓝色_2 / 蓝色(2) / 蓝色-3
_DUP_SUFFIX = re.compile(r"[_\-\s]*[（(]?\d+[)）]?$")
_BRACKET_COLOR = re.compile(r"[（(]([^（）()]+)[)）]\s*$")
_PUNCT = re.compile(r"[\s\-_·・/\\()（）\[\]【】,，.。:：;；'\"“”‘’&]")


def norm(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).casefold()
    return _PUNCT.sub("", t)


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_image(name: str) -> bool:
    return ext_of(name) in IMAGE_EXT


def clean_color(stem: str) -> str:
    """从白底图文件名还原颜色（沿用 V0.3 已验证的三条规则）。"""
    inner = _BRACKET_COLOR.search(stem)
    if inner and not re.search(r"版|GB|G\b", inner.group(1)):
        return inner.group(1).strip()
    return _DUP_SUFFIX.sub("", stem).strip() or stem


def color_from_filename(stem: str, kind: str, brand: str, model: str) -> tuple[str, str]:
    """返回 (color, color_source)。

    只对白底图做颜色抽取：营销图的文件名是「型号_颜色」或「型号_1」，
    在没有标准型号库的前提下拆不准，**宁可不给，也不猜**。
    """
    if kind != "white":
        return "", "none"
    s = (stem or "").strip()
    if not s:
        return "", "none"
    if re.fullmatch(r"\d{8,}", s):          # 时间戳文件名
        return "", "none"
    if any(w in s for w in USAGE_WORDS):
        return "", "usage-word"
    color = clean_color(s)
    ns, nb, nm = norm(s), norm(brand), norm(model)
    if nm and (ns == nm or ns.startswith(nm)):
        return "", "none"                   # 文件名就是型号名，不含颜色
    if nb and ns == nb:
        return "", "none"
    # 颜色必须含中文，或含已知英文色名；否则视为哈希/编号/型号串（如 48f9b5cfa337bf82）
    if not re.search(r"[\u4e00-\u9fff]", color) and not any(w in color.lower() for w in LATIN_COLORS):
        return "", "none"
    return color, "filename"


def resolve(path: str, name: str | None = None) -> dict[str, Any] | None:
    """解析一条飞书节点路径；不是支持的来源则返回 None。

    返回字段：
      source_channel / asset_kind / shop_id
      category / category_source（dir=目录声明, missing=目录缺类目层）
      brand_dir / model_dir / filename / ext
      color / color_source
      depth_note（结构异常说明）
    """
    segs = [p for p in (path or "").split("/") if p]
    if not segs:
        return None
    src = _SOURCE_BY_ROOT.get(segs[0])
    if not src:
        return None

    filename = name or segs[-1]
    idx = 1
    shop_id = ""
    if src["has_shop"]:
        if idx >= len(segs):
            return None
        shop_id = segs[idx]
        idx += 1

    category, category_source = categories.UNKNOWN_CATEGORY, "missing"
    if idx < len(segs):
        cid = categories.resolve_dir(segs[idx])
        if cid != categories.UNKNOWN_CATEGORY:
            category, category_source = cid, "dir"
            idx += 1

    brand_dir = segs[idx] if idx < len(segs) else ""
    rest = segs[idx + 1:] if idx < len(segs) else []

    model_dir, depth_note = "", ""
    kind = src["asset_kind"]
    if len(rest) >= 2:
        model_dir = rest[0]
        if len(rest) >= 3:                  # 型号目录下还有一层（商详/详情）
            if rest[0] in DETAIL_DIRS or any(r in DETAIL_DIRS for r in rest[:-1]):
                kind = "detail"
                depth_note = "商品详情长图，不是主图"
            else:
                depth_note = "型号目录下仍有子目录，按最后一层文件归属该型号"
    elif len(rest) == 1:
        depth_note = "品牌层直放文件，无型号目录"
    else:
        depth_note = "缺少文件名层级"

    if kind == "detail" and model_dir in DETAIL_DIRS:
        model_dir = rest[1] if len(rest) >= 2 else ""

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    color, color_source = color_from_filename(stem, kind, brand_dir, model_dir)

    return {
        "source_channel": src["id"],
        "asset_kind": kind,
        "shop_id": shop_id,
        "category": category,
        "category_source": category_source,
        "brand_dir": brand_dir,
        "model_dir": model_dir,
        "filename": filename,
        "ext": ext_of(filename),
        "color": color,
        "color_source": color_source,
        "depth_note": depth_note,
    }


def supported_roots() -> tuple[str, ...]:
    return tuple(s["root"] for s in SOURCE_SPECS)


def source_label(channel_id: str) -> str:
    for s in SOURCE_SPECS:
        if s["id"] == channel_id:
            return s["label"]
    return channel_id or "—"
