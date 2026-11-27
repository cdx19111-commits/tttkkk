"""Douyin shop directory: shop id to display name and owner."""
from __future__ import annotations

SHOP_DIRECTORY: dict[str, dict[str, str]] = {
    "129675602": {"name": "爱回收官方旗舰店", "owner": "许杨"},
    "25565842": {"name": "爱回收严选二手手机", "owner": "王文鲜"},
    "636781": {"name": "爱回收二手手机旗舰店", "owner": "王隆西"},
    "7970406": {"name": "爱回收严选二手数码", "owner": "金仲伟"},
    "51165770": {"name": "爱回收严选二手优品", "owner": "吕祖东"},
    "25595926": {"name": "拍拍二手悦玥手机专卖店", "owner": "许杨"},
    "168976032": {"name": "拍拍严选手机优品", "owner": "王志娟"},
    "5576543": {"name": "拍拍严选二手手机", "owner": "耿旭虹"},
    "156450040": {"name": "拍拍二手数码", "owner": "王隆西"},
    "208501840": {"name": "返航新生二手手机旗舰店", "owner": "赵国安"},
}


def shop_name(shop_id: object) -> str:
    info = SHOP_DIRECTORY.get(str(shop_id))
    return info["name"] if info else str(shop_id)


def shop_owner(shop_id: object) -> str:
    info = SHOP_DIRECTORY.get(str(shop_id))
    return info["owner"] if info else ""


def shop_label(shop_id: object) -> str:
    """Display label used by filters and previews: name（owner）."""
    info = SHOP_DIRECTORY.get(str(shop_id))
    if not info:
        return str(shop_id)
    return f"{info['name']}（{info['owner']}）" if info.get("owner") else info["name"]
