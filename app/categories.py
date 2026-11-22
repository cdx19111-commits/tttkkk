"""V2.0 品类配置：多类目图库的唯一"品类知识"入口。

设计原则
--------
1. **新增品类只改本文件**，不改采集、匹配、API、前端任何代码。
2. 品类用稳定英文 id 存库（`phone` / `tablet` / `watch` / `laptop`），
   中文名只用于展示；目录名、盖亚 cat3Name 均为别名列表，可多对一。
3. 品类专属的规格维度写在 `spec_dims` 里，匹配层据此决定要不要做
   颜色/内存/表带等下探；**不假设所有品类都有手机的颜色与内存**。
4. `model_index` 声明该类目的标准型号来源：
   - `gaia`   ：有盖亚标准库，可做型号/颜色级匹配；
   - `folder` ：暂无盖亚数据，用飞书目录型号做临时索引（必须标注"未标准化"）。
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 品类定义
# ---------------------------------------------------------------------------
CATEGORY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "phone",
        "label": "手机",
        "order": 10,
        "dir_names": ("手机",),
        "gaia_cat3": ("手机",),
        "spec_dims": ("color", "memory"),
        "model_index": "gaia",
        "note": "已建成类目，V1.8.x 主线；本轮只做兼容，不重跑",
    },
    {
        "id": "tablet",
        "label": "平板电脑",
        "order": 20,
        "dir_names": ("平板", "平板电脑"),
        "gaia_cat3": ("平板", "平板电脑"),
        "spec_dims": ("color",),
        "model_index": "folder",
        "note": "一期接入类目；盖亚未接入时用目录型号临时索引",
    },
    {
        "id": "watch",
        "label": "智能手表",
        "order": 30,
        "dir_names": ("手表", "智能手表", "腕表"),
        "gaia_cat3": ("智能手表", "手表"),
        "spec_dims": ("color", "band", "case"),
        "model_index": "folder",
        "note": "二期候选；SKU 语义为表带/表壳，不与手机共用",
    },
    {
        "id": "laptop",
        "label": "笔记本",
        "order": 40,
        "dir_names": ("笔记本", "笔记本电脑"),
        "gaia_cat3": ("笔记本", "笔记本电脑"),
        "spec_dims": ("config",),
        "model_index": "folder",
        "note": "资产仅 16 张，暂不纳入业务；配置维度待定义",
    },
)

# 未识别到品类时的兜底 id（不要静默归到手机）
UNKNOWN_CATEGORY = "unknown"
UNKNOWN_LABEL = "未识别品类"

# 素材类型（用途维度，与品类正交）
ASSET_KINDS: tuple[dict[str, str], ...] = (
    {"id": "white", "label": "白底图", "note": "可用于主图/白底交付"},
    {"id": "marketing", "label": "营销图", "note": "渠道营销素材，含水印/文案/模板"},
    {"id": "detail", "label": "详情长图", "note": "商品详情页长图，不是主图"},
)

# 发布状态（决定原图/下载是否放行）
PUBLISH_STATES: tuple[dict[str, str], ...] = (
    {"id": "published", "label": "已发布", "note": "原图与下载均放行"},
    {"id": "candidate", "label": "候选", "note": "仅缩略图预览，禁止原图与下载"},
    {"id": "restricted", "label": "受限", "note": "不允许任何图片资源访问"},
)

_BY_ID = {c["id"]: c for c in CATEGORY_SPECS}
_BY_DIR = {name: c["id"] for c in CATEGORY_SPECS for name in c["dir_names"]}
_KIND_BY_ID = {k["id"]: k for k in ASSET_KINDS}
_PUBLISH_BY_ID = {s["id"]: s for s in PUBLISH_STATES}


def all_specs() -> tuple[dict[str, Any], ...]:
    """按展示顺序返回全部品类（含未启用的）。"""
    return tuple(sorted(CATEGORY_SPECS, key=lambda c: c["order"]))


def ids() -> tuple[str, ...]:
    return tuple(c["id"] for c in all_specs())


def get(category_id: str) -> dict[str, Any] | None:
    return _BY_ID.get((category_id or "").strip().lower())


def label(category_id: str) -> str:
    spec = get(category_id)
    return spec["label"] if spec else UNKNOWN_LABEL


def resolve_dir(name: str) -> str:
    """把目录名（手机/平板/手表…）解析成品类 id；解析不到返回 unknown。"""
    return _BY_DIR.get((name or "").strip(), UNKNOWN_CATEGORY)


def spec_dims(category_id: str) -> tuple[str, ...]:
    spec = get(category_id)
    return tuple(spec["spec_dims"]) if spec else ()


def model_index_kind(category_id: str) -> str:
    spec = get(category_id)
    return spec["model_index"] if spec else "folder"


def kind_label(kind_id: str) -> str:
    return _KIND_BY_ID.get(kind_id, {}).get("label", kind_id or "—")


def publish_label(state: str) -> str:
    return _PUBLISH_BY_ID.get(state, {}).get("label", state or "—")


def public_catalog() -> dict[str, Any]:
    """给前端/接口的品类字典（不含内部实现细节）。"""
    return {
        "categories": [
            {"id": c["id"], "label": c["label"], "order": c["order"]}
            for c in all_specs()
        ],
        "asset_kinds": [{"id": k["id"], "label": k["label"]} for k in ASSET_KINDS],
        "publish_states": [{"id": s["id"], "label": s["label"]} for s in PUBLISH_STATES],
    }
