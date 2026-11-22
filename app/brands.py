"""品牌别名与品牌组：V2 起作为**唯一品牌字典**（API / 采集 / 匹配共用）。

与 V0.3 相比只做扩展，不改既有映射：
- 新增手表品牌：佳明 / 颂拓 / 高驰 / 北斗 / 华米 / 小天才
- 新增平板/笔记本品牌：酷比魔方 / ThinkPad
- 小米系、vivo系、OPPO系三个品牌组保持不变
"""
from __future__ import annotations

# 别名键（小写、去空格） → 品牌稳定键
BRAND_ALIASES: dict[str, str] = {
    "apple": "apple", "苹果": "apple", "苹果笔记本": "apple", "苹果电脑": "apple",
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
    # 手表品牌（66 号盘点新增）
    "garmin": "garmin", "佳明": "garmin",
    "suunto": "suunto", "颂拓": "suunto",
    "coros": "coros", "高驰": "coros",
    "beidou": "beidou", "北斗": "beidou",
    "amazfit": "amazfit", "华米": "amazfit",
    "xtc": "xtc", "小天才": "xtc",
    # 平板 / 笔记本品牌
    "kubimofang": "kubimofang", "酷比魔方": "kubimofang", "alldocube": "kubimofang",
    "thinkpad": "thinkpad", "thinkbook": "thinkpad",
    "1+": "oneplus",
}

# 品牌稳定键 → 展示名（与现有目录写法保持一致，避免界面突变）
BRAND_LABELS: dict[str, str] = {
    "apple": "Apple", "iqoo": "iQOO", "oppo": "OPPO", "vivo": "vivo",
    "redmi": "Redmi", "honor": "荣耀", "huawei": "华为", "xiaomi": "小米",
    "oneplus": "一加", "samsung": "三星", "realme": "真我", "nubia": "努比亚",
    "lenovo": "联想", "garmin": "佳明", "suunto": "颂拓", "coros": "高驰",
    "beidou": "北斗", "amazfit": "华米", "xtc": "小天才",
    "kubimofang": "酷比魔方", "thinkpad": "ThinkPad",
}

# 只登记多子品牌集团；其余品牌独立成行（与现有前端分组一致）
BRAND_GROUPS: tuple[dict, ...] = (
    {"id": "xiaomi", "label": "小米系", "keys": ("xiaomi", "redmi")},
    {"id": "vivo", "label": "vivo系", "keys": ("vivo", "iqoo")},
    {"id": "oppo", "label": "OPPO系", "keys": ("oppo", "oneplus", "realme")},
)


# 母公司/子品牌关联：机身印母公司 logo 不算品牌冲突（如 ThinkPad 机身印 Lenovo）
# 仅用于核验判定，不改展示层、不写库。
BRAND_AFFILIATIONS: dict[str, set[str]] = {
    "lenovo": {"thinkpad", "thinkbook"},
    "thinkpad": {"lenovo"},
    "xiaomi": {"redmi"},
    "redmi": {"xiaomi"},
    "apple": {"ipad", "iphone", "macbook"},   # 产品线标识，还需目录型号包含该词才判定
}


def affiliated(key_a: str, key_b: str) -> bool:
    return key_b in BRAND_AFFILIATIONS.get(key_a, ())


def brand_key(name: str) -> str:
    """任意写法 → 品牌稳定键；未知品牌返回去空格小写原文。"""
    k = (name or "").strip().casefold()
    return BRAND_ALIASES.get(k, k)


def brand_label(name: str) -> str:
    """任意写法 → 展示名；未知品牌保留原写法（不丢信息）。"""
    k = brand_key(name)
    return BRAND_LABELS.get(k, (name or "").strip())


def variants(keys) -> tuple[str, ...]:
    """把一个品牌组的全部已知写法展开成 SQL IN 列表（小写）。"""
    wanted = set(keys)
    out = set(wanted)
    out |= {alias for alias, key in BRAND_ALIASES.items() if key in wanted}
    return tuple(sorted(out))
