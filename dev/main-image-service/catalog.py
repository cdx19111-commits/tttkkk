"""Catalog helpers: shop/model matching and source-image selection."""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import sys

for _parent in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if (_parent / "app" / "brands.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from app import brands  # noqa: E402

_PUNCT = re.compile(r"[\s\-_·・/\\()（）\[\]【】,，.。:：;；'\"“”‘’&]+")


def _norm(value: object) -> str:
    return _PUNCT.sub("", unicodedata.normalize("NFKC", str(value or "")).casefold())


_ALL_BRANDS = sorted(
    {_norm(a) for a in brands.BRAND_ALIASES}
    | {_norm(k) for k in set(brands.BRAND_ALIASES.values())},
    key=len,
    reverse=True,
)


def brand_key(value: str) -> str:
    cleaned = re.sub(r"[（(].*?[)）]", "", str(value or "")).strip()
    return brands.brand_key(cleaned)


def model_key(value: str) -> str:
    normalized = _norm(value)
    for alias in _ALL_BRANDS:
        if alias and normalized.startswith(alias) and len(normalized) > len(alias):
            return normalized[len(alias):]
    return normalized


def brand_group(value: str) -> str:
    key = brand_key(value)
    for group in brands.BRAND_GROUPS:
        if key in group["keys"]:
            return group["id"]
    return key


def orientation_ok(category: str, size: tuple[int, int]) -> bool:
    """A product cutout should not be a side view or a flat-lay strip."""
    width, height = size
    if not width or not height:
        return False
    ratio = width / height
    if category == "phone":
        return 0.30 <= ratio <= 1.08
    if category == "tablet":
        return 0.55 <= ratio <= 1.65
    if category == "watch":
        return 0.55 <= ratio <= 1.25
    return True


def target_key(group: str, model: str) -> str:
    """Unique per-shop model key: brand group + model key."""
    return f"{group}:{model}" if group else model


def is_black(color: str) -> bool:
    text = str(color or "").casefold()
    return any(word in text for word in ("黑", "black", "午夜", "曜石", "墨", "玄"))


@dataclass
class ExistingMain:
    item_id: str
    source_path: str
    name: str
    extension: str
    local_path: str
    width: int | None
    height: int | None

    @property
    def stem(self) -> str:
        return Path(self.name).stem


@dataclass
class SourceAsset:
    asset_id: str
    model_id: str
    model_name: str
    color: str
    local_path: str
    width: int | None
    height: int | None
    sha256: str = ""
    brand: str = ""
    category: str = "phone"


@dataclass
class Target:
    model_name: str
    brand: str
    group: str
    key: str
    template_key: str
    existing: ExistingMain
    sources: list[SourceAsset] = field(default_factory=list)
    shop_id: str = ""
    category: str = "phone"
    brand_label: str = ""


def load_targets(shop_id: str, wanted: list[str], db_path: Path) -> list[Target]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        shop_items: dict[tuple[str, str], list[ExistingMain]] = defaultdict(list)
        for row in conn.execute(
            "select item_id, source_path, name, extension, local_path, width, height "
            "from douyin_gallery_items where source_path like ?",
            (f"抖音主图/{shop_id}/%",),
        ):
            parts = str(row["source_path"]).split("/")
            if len(parts) < 6:
                continue
            shop_items[(brand_group(parts[3]), model_key(parts[4]))].append(
                ExistingMain(row["item_id"], row["source_path"], row["name"],
                             row["extension"], row["local_path"], row["width"], row["height"])
            )

        sources: dict[tuple[str, str], list[SourceAsset]] = defaultdict(list)
        for row in conn.execute(
            "select id, model_id, coalesce(nullif(model_name,''), model_dir), "
            "coalesce(nullif(display_color,''), nullif(standard_color,''), color), "
            "local_path, width, height, coalesce(sha256,''), "
            "coalesce(nullif(brand_name,''), nullif(display_brand,''), brand_dir, '') "
            "from image_assets where category='phone' and asset_kind='white' "
            "and quality_level='A'"
        ):
            sources[(brand_group(row[8]), model_key(row[2]))].append(
                SourceAsset(row[0], str(row[1] or ""), row[2], row[3] or "",
                            row[4], row[5], row[6], row[7], row[8], "phone")
            )
    finally:
        conn.close()

    result: list[Target] = []
    for wanted_name in wanted:
        wanted_key = model_key(wanted_name)
        candidates = [
            (key, items) for key, items in shop_items.items()
            if key[1] == wanted_key and key in sources and items
        ]
        if not candidates:
            raise RuntimeError(f"sample target not found: {wanted_name}")
        # Prefer the exact brand group from the requested name; otherwise first.
        key, items = sorted(candidates, key=lambda x: (x[0][0], -len(x[1])))[0]
        model_name = str(items[0].source_path).split("/")[4]
        template_key = "ios_phone" if key[0] == "apple" else "android_phone"
        result.append(Target(model_name=model_name, brand=key[0], group=key[0],
                             key=target_key(key[0], key[1]), template_key=template_key,
                             existing=sorted(items, key=lambda x: (x.local_path, x.item_id))[0],
                             sources=sources[key], shop_id=shop_id, category="phone",
                             brand_label=key[0]))
    return result


_CATEGORY_FOLDERS = {"手机", "平板", "手表", "笔记本", "其他数码"}


def _file_stem(name: str, brand: str = "") -> str:
    stem = re.sub(r"\.(png|jpe?g|webp)$", "", str(name or ""), flags=re.I)
    if brand:
        for prefix in {brand, brand.replace(" ", ""), brands.brand_key(brand)}:
            if prefix and stem.startswith(prefix):
                stem = stem[len(prefix):].lstrip("_- ")
                break
    # Flat files use _color suffixes; keep hyphens in model names.
    return re.split(r"_", stem)[0].strip() or stem


def _template_for(category: str, brand_group_value: str) -> tuple[str, str]:
    if not category or category in {"手机", "phone"}:
        return ("ios_phone" if brand_group_value == "apple" else "android_phone", "phone")
    if category == "平板":
        return ("ipad" if brand_group_value == "apple" else "android_tablet", "tablet")
    if category == "手表":
        return ("watch", "watch")
    return ("other_digital", "laptop")


def _parse_item(source_path: str):
    parts = str(source_path).split("/")
    if len(parts) >= 6:
        return parts[2], parts[3], parts[4]
    if len(parts) == 5:
        if parts[2] in _CATEGORY_FOLDERS:
            return parts[2], parts[3], _file_stem(parts[4], parts[3])
        return "", parts[2], _file_stem(parts[3], parts[2])
    if len(parts) == 4:
        return "", parts[2], _file_stem(parts[3], parts[2])
    return None


def load_shop_targets(shop_id: str, db_path: Path) -> list[Target]:
    """All model folders/files in one shop that have an A-level white source."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        shop_items: dict[tuple[str, str], list[tuple[ExistingMain, str, str]]] = defaultdict(list)
        columns = {row[1] for row in conn.execute("pragma table_info(douyin_gallery_items)")}
        active_filter = "coalesce(is_active,1)=1" if "is_active" in columns else "1=1"
        for row in conn.execute(
            "select item_id, source_path, name, extension, local_path, width, height "
            f"from douyin_gallery_items where source_path like ? and {active_filter}",
            (f"抖音主图/{shop_id}/%",),
        ):
            parsed = _parse_item(row["source_path"])
            if not parsed:
                continue
            category, brand, model = parsed
            existing = ExistingMain(row["item_id"], row["source_path"], row["name"],
                                    row["extension"], row["local_path"],
                                    row["width"], row["height"])
            shop_items[(brand_group(brand), model_key(model))].append((existing, category, brand))

        sources: dict[tuple[str, str], list[SourceAsset]] = defaultdict(list)
        for row in conn.execute(
            "select id, model_id, coalesce(nullif(model_name,''), model_dir), "
            "coalesce(nullif(display_color,''), nullif(standard_color,''), color), "
            "local_path, width, height, coalesce(sha256,''), "
            "coalesce(nullif(brand_name,''), nullif(display_brand,''), brand_dir, '') "
            "from image_assets where category='phone' and asset_kind='white' "
            "and quality_level='A'"
        ):
            sources[(brand_group(row[8]), model_key(row[2]))].append(
                SourceAsset(row[0], str(row[1] or ""), row[2], row[3] or "",
                            row[4], row[5], row[6], row[7], row[8], "phone"))
    finally:
        conn.close()

    result: list[Target] = []
    for key, grouped in shop_items.items():
        if key not in sources:
            continue
        existing, category, brand = sorted(grouped, key=lambda x: (x[0].local_path, x[0].item_id))[0]
        template_key, normalized_category = _template_for(category, key[0])
        model_name = _file_stem(existing.name, brand) if not category else (
            existing.source_path.split("/")[4] if len(existing.source_path.split("/")) >= 6
            else _file_stem(existing.name, brand))
        if not model_name:
            model_name = sources[key][0].model_name
        result.append(Target(model_name=model_name, brand=key[0], group=key[0],
                             key=target_key(key[0], key[1]), template_key=template_key, existing=existing,
                             sources=sources[key], shop_id=shop_id,
                             category=normalized_category, brand_label=brand))
    return sorted(result, key=lambda x: (x.category, x.brand, x.model_name))


def load_all_sources(db_path: Path) -> dict[tuple[str, str], list[SourceAsset]]:
    """All A-level phone white sources grouped by (brand group, model key)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    sources: dict[tuple[str, str], list[SourceAsset]] = defaultdict(list)
    try:
        for row in conn.execute(
            "select id, model_id, coalesce(nullif(model_name,''), model_dir), "
            "coalesce(nullif(display_color,''), nullif(standard_color,''), color), "
            "local_path, width, height, coalesce(sha256,''), "
            "coalesce(nullif(brand_name,''), nullif(display_brand,''), brand_dir, '') "
            "from image_assets where category='phone' and asset_kind='white' "
            "and quality_level='A'"
        ):
            sources[(brand_group(row[8]), model_key(row[2]))].append(
                SourceAsset(row[0], str(row[1] or ""), row[2], row[3] or "",
                            row[4], row[5], row[6], row[7], row[8], "phone"))
    finally:
        conn.close()
    return sources
