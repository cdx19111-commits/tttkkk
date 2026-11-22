"""Read-only phone listing API for the authenticated internal service clients."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from . import storage_contract as assets_api
from . import brands

router = APIRouter(prefix="/openapi/v1", tags=["Douyin listing API"])
GAIA_POOL_PATH = Path(os.getenv("GAIA_MODEL_POOL_PATH", "/app/app/gaia_runtime/gaia_model_pool.json"))
# The requirement says 3 MB; use decimal MB so the implementation does not
# accidentally accept files above 3,000,000 bytes.
MAX_IMAGE_BYTES = 3_000_000
SUPPORTED_FORMATS = {"jpg", "jpeg", "png", "webp"}
_PUNCT = re.compile(r"[\s\-_·・/\\()（）\[\]【】,，.。:：;；'\"“”‘’&]+")


class ListingAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str,
                 details: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Need(_StrictModel):
    main: bool = True
    min_whites_with_main: int = Field(default=2, ge=0, le=50)
    min_whites_without_main: int = Field(default=3, ge=0, le=50)
    color_images: bool = True
    max_whites: int = Field(default=4, ge=0, le=50)


class Options(_StrictModel):
    include_unpublished: bool = False
    allow_main_substitute: bool = True


class ResolveRequest(_StrictModel):
    request_id: str = Field(default="", max_length=128)
    shop_id: str = Field(min_length=1, max_length=80)
    category: str = "phone"
    brand: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=240)
    model_id: str = Field(default="", max_length=80)
    colors: list[str] = Field(default_factory=list, max_length=100)
    need: Need = Field(default_factory=Need)
    options: Options = Field(default_factory=Options)


class PrecheckItem(_StrictModel):
    shop_id: str = Field(min_length=1, max_length=80)
    category: str = "phone"
    brand: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=240)
    model_id: str = Field(default="", max_length=80)
    colors: list[str] = Field(default_factory=list, max_length=100)


class PrecheckRequest(_StrictModel):
    request_id: str = Field(default="", max_length=128)
    need: Need = Field(default_factory=Need)
    options: Options = Field(default_factory=Options)
    items: list[PrecheckItem] = Field(min_length=1, max_length=50)


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return _PUNCT.sub("", text)


def _model_brand_keys(model: dict) -> set[str]:
    values = (model.get("brand"), model.get("gaia_brand"), model.get("brand_name"))
    return {brands.brand_key(str(x).strip()) for x in values if str(x or "").strip()}


def _brand_aliases_for(model: dict) -> set[str]:
    keys = _model_brand_keys(model)
    aliases = {alias for alias, key in brands.BRAND_ALIASES.items() if key in keys}
    aliases.update(str(x).strip() for x in
                   (model.get("brand"), model.get("gaia_brand"), model.get("brand_name"))
                   if str(x or "").strip())
    return aliases


def _model_aliases(model: dict) -> set[str]:
    names = {str(model.get("model") or "").strip(), str(model.get("model_name") or "").strip()}
    for name in tuple(names):
        normalized = _norm(name)
        for brand_alias in _brand_aliases_for(model):
            prefix = _norm(brand_alias)
            if prefix and normalized.startswith(prefix) and len(normalized) > len(prefix):
                names.add(normalized[len(prefix):])
    return {_norm(name) for name in names if _norm(name)}


def _gaia_pool() -> tuple[list[dict], dict[str, dict]]:
    try:
        payload = json.loads(GAIA_POOL_PATH.read_text(encoding="utf-8"))
        models = payload["models"]
        if not isinstance(models, list) or not models:
            raise ValueError("models must be a non-empty list")
        by_id: dict[str, dict] = {}
        for entry in models:
            mid = str(entry.get("model_id") or "").strip()
            if not mid or mid in by_id:
                raise ValueError("Gaia model IDs are missing or duplicated")
            if "手机" not in str(entry.get("category") or ""):
                continue
            by_id[mid] = entry
        if not by_id:
            raise ValueError("Gaia snapshot contains no phone models")
        return list(by_id.values()), by_id
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ListingAPIError(503, "MODEL_INDEX_UNAVAILABLE",
                              "Gaia 手机型号索引暂不可用") from None


def _check_shop(request: Request, shop_id: str) -> None:
    client = getattr(request.state, "service_client", None)
    allowed = client.allowed_shop_ids if client else frozenset()
    if allowed and shop_id not in allowed:
        raise ListingAPIError(403, "FORBIDDEN", "该 Token 无权访问此店铺")


def _resolve_model(model_id: str, brand: str, model_name: str) -> tuple[dict | None, str, list[dict]]:
    models, by_id = _gaia_pool()
    requested_brand = brands.brand_key(brand) if brand else ""
    name_key = _norm(model_name)
    if model_id:
        match = by_id.get(str(model_id).strip())
        if not match:
            return None, "not_found", []
        if requested_brand and requested_brand not in _model_brand_keys(match):
            raise ListingAPIError(400, "INVALID_ARGUMENT", "brand 与 model_id 对应的 Gaia 品牌不一致")
        if name_key and name_key not in _model_aliases(match):
            raise ListingAPIError(400, "INVALID_ARGUMENT", "model 与 model_id 对应的 Gaia 型号不一致")
        return match, "exact", []
    if not brand or not model_name:
        raise ListingAPIError(400, "INVALID_ARGUMENT", "请提供 model_id，或同时提供 brand 和完整 model")
    candidates = [m for m in models
                  if requested_brand in _model_brand_keys(m) and name_key in _model_aliases(m)]
    if len(candidates) == 1:
        return candidates[0], "exact", []
    listed = [{"brand_id": str(x.get("brand_id") or ""),
               "brand_name": str(x.get("gaia_brand") or x.get("brand") or ""),
               "model_id": str(x.get("model_id") or ""),
               "model_name": str(x.get("model") or "")} for x in candidates]
    if candidates:
        return None, "ambiguous", listed
    return None, "not_found", []


def _model_output(model: dict) -> dict:
    return {"brand_id": str(model.get("brand_id") or ""),
            "brand_name": str(model.get("gaia_brand") or model.get("brand") or ""),
            "brand_aliases": sorted(_brand_aliases_for(model)),
            "display_brand": str(model.get("brand") or ""),
            "model_id": str(model.get("model_id") or ""),
            "model_name": str(model.get("model") or ""),
            "aliases": sorted(x for x in _model_aliases(model)
                              if x != _norm(model.get("model") or "")),
            "category": "phone",
            "colors": list(model.get("colors") or [])}


def _format(value: object) -> str:
    value = str(value or "").strip().lower().lstrip(".")
    return "jpg" if value == "jpeg" else value


@lru_cache(maxsize=8192)
def _hash_file(path_text: str, mtime_ns: int, size: int) -> str:
    path = Path(path_text)
    digest = hashlib.sha256()
    with path.open("rb") as image:
        for chunk in iter(lambda: image.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_path(local_path: str) -> Path | None:
    if not local_path:
        return None
    root = assets_api.ASSET_ROOT.resolve()
    candidate = (root / local_path).resolve()
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def _asset_hash(row: dict, path: Path) -> str:
    existing = str(row.get("sha256") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", existing):
        return existing
    stat = path.stat()
    return _hash_file(str(path), stat.st_mtime_ns, stat.st_size)


def _flag_state(flags: dict, key: str, expected) -> str:
    value = flags.get(key)
    if key == "white_background":
        if value is True or str(value).casefold() in {"yes", "true", "1"}:
            return "yes"
        if value is False or str(value).casefold() in {"no", "false", "0"}:
            return "no"
        return "unknown"
    if key == "single_product":
        if value is True or str(value).casefold() in {"yes", "true", "1"}:
            return "yes"
        if value is False or str(value).casefold() in {"no", "false", "0"}:
            return "no"
        return "unknown"
    if key == "marketing_or_watermark":
        if value is False or str(value).casefold() in {"no", "false", "0"}:
            return "no"
        if value is True or str(value).casefold() in {"yes", "true", "1"}:
            return "yes"
        return "unknown"
    return "unknown"


def _evaluate_white(row: dict) -> dict:
    reasons: list[dict] = []
    status = "eligible"
    fmt = _format(row.get("format"))
    if fmt not in SUPPORTED_FORMATS:
        status = "ineligible"
        reasons.append({"code": "unsupported_format", "text": f"不支持 {row.get('format') or '未知'} 格式"})
    width, height = row.get("width"), row.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        status = "ineligible"
        reasons.append({"code": "invalid_dimensions", "text": "图片尺寸缺失或无效"})
    else:
        if width != height:
            status = "ineligible"
            reasons.append({"code": "not_square", "text": f"{width}×{height}，不是正方形"})
        if min(width, height) < 600:
            status = "ineligible"
            reasons.append({"code": "too_small", "text": f"{width}×{height}，短边小于 600 像素"})
    try:
        size = int(row.get("size_bytes") or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        status = "ineligible"
        reasons.append({"code": "invalid_size", "text": "文件大小缺失或无效"})
    elif size > MAX_IMAGE_BYTES:
        status = "ineligible"
        reasons.append({"code": "too_large", "text": "文件超过 3 MiB"})

    try:
        flags = json.loads(row.get("quality_flags") or "{}")
        if not isinstance(flags, dict):
            flags = {}
    except (TypeError, ValueError, json.JSONDecodeError):
        flags = {}
    states = {"white_background": _flag_state(flags, "white_background", "yes"),
              "single_product": _flag_state(flags, "single_product", True),
              "watermark": _flag_state(flags, "marketing_or_watermark", "no")}
    for key, bad_state, code, text in (("white_background", "no", "not_white", "未确认白底背景"),
                                       ("single_product", "no", "multi_product", "未确认画面只有一件商品"),
                                       ("watermark", "yes", "watermark", "未确认无水印或营销文字")):
        state = states[key]
        if state == bad_state:
            status = "ineligible"
            reasons.append({"code": code, "text": text.replace("未确认", "不符合")})
        elif state == "unknown" and status == "eligible":
            status = "unknown"
            reasons.append({"code": "visual_evidence_missing", "text": text})

    path = _asset_path(str(row.get("local_path") or ""))
    sha = ""
    if path is None:
        if status == "eligible":
            status = "unknown"
        reasons.append({"code": "file_missing", "text": "图库原始文件当前不可读取"})
    else:
        try:
            sha = _asset_hash(row, path)
        except OSError:
            if status == "eligible":
                status = "unknown"
            reasons.append({"code": "hash_unavailable", "text": "无法校验图片 SHA-256"})
    return {"status": status, "reasons": reasons, "flags": states,
            "sha256": sha, "path": path, "format": fmt}


def _white_rows(model_id: str) -> list[dict]:
    with assets_api.db() as conn:
        rows = conn.execute("""select * from image_assets
            where coalesce(category,'phone')='phone' and model_id=?
              and coalesce(quality_level,'B')='A'
              and coalesce(asset_kind,'white')='white'
            order by coalesce(standard_color,''), coalesce(display_filename,filename), id""",
            (model_id,)).fetchall()
    evaluated: list[dict] = []
    seen: set[str] = set()
    for db_row in rows:
        row = dict(db_row)
        outcome = _evaluate_white(row)
        row["_qualification"] = outcome
        if outcome["status"] == "eligible":
            sha = outcome["sha256"]
            if sha in seen:
                outcome["status"] = "ineligible"
                outcome["reasons"].append({"code": "duplicate", "text": "同一型号已有相同 SHA-256 图片"})
            else:
                seen.add(sha)
        evaluated.append(row)
    return evaluated


def _phone_model_path(path_text: str, shop_id: str, model: dict) -> bool:
    parts = str(path_text or "").strip("/").split("/")
    if len(parts) < 5 or parts[0] != "抖音主图" or parts[1] != shop_id:
        return False
    directory_parts = parts[2:-1]
    model_names = _model_aliases(model)
    model_indexes = [i for i, segment in enumerate(directory_parts)
                     if _norm(segment) in model_names]
    expected_brands = {_norm(x) for x in _brand_aliases_for(model) if _norm(x)}
    expected_brands |= {_norm(model.get("brand") or ""), _norm(model.get("gaia_brand") or "")}
    has_brand_in_path = any(_norm(segment) in expected_brands for segment in directory_parts)
    file_stem = Path(parts[-1]).stem
    file_key = _norm(file_stem)
    exact_model_file = file_key in model_names or any(
        re.fullmatch(re.escape(alias) + r"[0-9]+", file_key) for alias in model_names)
    if exact_model_file and has_brand_in_path:
        return True
    if not model_indexes:
        return False
    for model_index in model_indexes:
        preceding = directory_parts[:model_index]
        has_brand = any(_norm(segment) in expected_brands for segment in preceding)
        has_phone = any(_norm(segment) in {_norm("手机"), _norm("phone")} for segment in preceding)
        if has_brand and (has_phone or model_index >= 1):
            return True
    return False


def _main_rows(shop_id: str, model: dict) -> list[dict]:
    prefix = f"抖音主图/{shop_id}/"
    with assets_api.db() as conn:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        if "douyin_gallery_items" not in tables:
            raise ListingAPIError(503, "MAIN_LIBRARY_UNAVAILABLE", "抖音主图索引暂不可用")
        rows = conn.execute("""select item_id,source_path,name,extension,local_path,
              sha256,size_bytes,width,height,imported_at
            from douyin_gallery_items where substr(source_path,1,length(?))=?
            order by source_path collate nocase,item_id""", (prefix, prefix)).fetchall()
    result = []
    seen: set[str] = set()
    for db_row in rows:
        row = dict(db_row)
        if not _phone_model_path(row.get("source_path", ""), shop_id, model):
            continue
        path = _asset_path(str(row.get("local_path") or ""))
        if path is None:
            continue
        try:
            sha = _asset_hash(row, path)
        except OSError:
            continue
        if sha in seen:
            continue
        seen.add(sha)
        row["sha256"] = sha
        result.append(row)
    return result


def _updated_at(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Source timestamps are ISO values. Preserve an existing timezone; expose UTC if absent.
    try:
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat(timespec="seconds")
    except ValueError:
        return ""


def _pool_updated_at() -> str:
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(GAIA_POOL_PATH.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return ""


def _white_object(row: dict, request: Request, model: dict) -> dict:
    q = row["_qualification"]
    image_id = str(row.get("id") or "")
    name = str(row.get("display_filename") or row.get("filename") or "")
    return {"image_id": image_id, "source": "white_library",
            "file_token": str(row.get("file_token") or ""),
            "source_path": str(row.get("source_path") or ""), "name": name,
            "format": str(row.get("format") or "").upper().replace("JPG", "JPEG"),
            "width": row.get("width"), "height": row.get("height"),
            "size_bytes": int(row.get("size_bytes") or 0), "sha256": q["sha256"],
            "brand_id": str(model.get("brand_id") or ""),
            "brand_name": str(model.get("gaia_brand") or model.get("brand") or ""),
            "model_id": str(model.get("model_id") or ""),
            "model_name": str(model.get("model") or ""),
            "color": str(row.get("display_color") or row.get("standard_color") or ""),
            "standard_color": str(row.get("standard_color") or ""), "color_aliases": [],
            "asset_kind": "white", "publish_state": "published",
            "quality_level": "A", "quality_flags": q["flags"],
            "douyin_eligible": q["status"] == "eligible",
            "eligibility_status": q["status"], "reject_reasons": q["reasons"],
            "rights_status": "confirmed", "usable_scope": "platform_listing",
            "external_use_allowed": True,
            "rights_basis": "user_approved_internal_api_callers",
            "duplicate_group": str(row.get("dup_group") or "") or None,
            "download_url": f"/openapi/v1/listing/images/{quote(image_id, safe='')}/file",
            "thumb_url": f"/openapi/v1/listing/images/{quote(image_id, safe='')}/file?w=320",
            "etag": f'"{q["sha256"]}"' if q["sha256"] else "",
            "updated_at": _updated_at(row.get("feishu_modified_at") or
                                        row.get("created_at"))}


def _main_object(row: dict, model: dict) -> dict:
    image_id = str(row.get("item_id") or "")
    name = str(row.get("name") or "")
    return {"image_id": image_id, "source": "douyin_main",
            "file_token": "", "source_path": str(row.get("source_path") or ""),
            "name": name, "format": str(row.get("extension") or "").upper().replace("JPG", "JPEG"),
            "width": row.get("width"), "height": row.get("height"),
            "size_bytes": int(row.get("size_bytes") or 0), "sha256": row["sha256"],
            "brand_id": str(model.get("brand_id") or ""),
            "brand_name": str(model.get("gaia_brand") or model.get("brand") or ""),
            "model_id": str(model.get("model_id") or ""),
            "model_name": str(model.get("model") or ""), "color": None,
            "standard_color": None, "color_aliases": [], "asset_kind": "main",
            "publish_state": "published", "quality_level": None,
            "quality_flags": None, "douyin_eligible": False,
            "eligibility_status": "unknown",
            "reject_reasons": [{"code": "visual_evidence_missing",
                                "text": "主图子图库没有白底/单机/水印结构化审核标记"}],
            "rights_status": "confirmed", "usable_scope": "platform_listing",
            "external_use_allowed": True,
            "rights_basis": "user_approved_internal_api_callers",
            "duplicate_group": None,
            "download_url": f"/openapi/v1/listing/images/{quote(image_id, safe='')}/file",
            "thumb_url": f"/openapi/v1/listing/images/{quote(image_id, safe='')}/file?w=320",
            "etag": f'"{row["sha256"]}"', "updated_at": _updated_at(row.get("imported_at"))}


def _color_results(colors: list[str], eligible: list[dict], excluded: list[dict],
                   request: Request, model: dict) -> tuple[list[dict], list[str]]:
    gaia_colors = list(dict.fromkeys(str(c).strip() for c in (model.get("colors") or []) if str(c).strip()))
    results, missing = [], []
    for requested in colors:
        key = _norm(requested)
        matching = [c for c in gaia_colors if _norm(c) == key]
        if not matching:
            results.append({"color": requested, "matched_color": None, "image": None,
                            "reason": "not_found", "reason_text": "输入颜色不属于该 Gaia 型号的标准颜色"})
            missing.append(requested)
            continue
        if len(matching) > 1:
            results.append({"color": requested, "matched_color": None, "image": None,
                            "reason": "ambiguous", "candidates": matching})
            missing.append(requested)
            continue
        canonical = matching[0]
        image_rows = [r for r in eligible
                      if _norm(r.get("standard_color")) == key]
        if len(image_rows) == 1:
            results.append({"color": requested, "matched_color": canonical,
                            "image": _white_object(image_rows[0], request, model),
                            "reason": None})
        elif len(image_rows) > 1:
            results.append({"color": requested, "matched_color": canonical, "image": None,
                            "reason": "ambiguous_images",
                            "candidates": [_white_object(r, request, model) for r in image_rows]})
            missing.append(requested)
        else:
            held = [r for r in excluded if _norm(r.get("standard_color")) == key]
            results.append({"color": requested, "matched_color": canonical, "image": None,
                            "reason": "not_eligible" if held else "not_found",
                            "reason_text": (held[0]["_qualification"]["reasons"][0]["text"]
                                            if held and held[0]["_qualification"]["reasons"]
                                            else "该颜色没有符合条件的白底图")})
            missing.append(requested)
    return results, missing


def _resolve(request_data: ResolveRequest, request: Request) -> dict:
    if request_data.category != "phone":
        raise ListingAPIError(400, "INVALID_ARGUMENT", "首期只支持 category=phone")
    if request_data.options.include_unpublished:
        raise ListingAPIError(400, "INVALID_ARGUMENT", "服务 API 只返回业务可用的 A 级图片")
    shop_id = request_data.shop_id.strip()
    _check_shop(request, shop_id)
    model, level, candidates = _resolve_model(request_data.model_id,
                                              request_data.brand, request_data.model)
    request_id = request_data.request_id or getattr(request.state, "request_id", "")
    if model is None:
        if level == "ambiguous":
            raise ListingAPIError(409, "MODEL_AMBIGUOUS",
                                  "型号在 Gaia 型号库中存在多个精确候选", {"candidates": candidates})
        return {"request_id": request_id, "matched": False,
                "match": {"level": "not_found", "brand_id": "", "brand_name": "",
                          "model_id": "", "model_name": ""},
                "main": None, "main_candidates": [], "whites": [], "color_images": [],
                "eligibility": {"main_found": False, "eligible_white_count": 0,
                                "required_whites": 0, "can_satisfy": False,
                                "all_colors_matched": False, "blocked_reason": "model_not_found"},
                "excluded": [], "model_coverage": {"white_total": 0, "white_eligible": 0,
                                                     "main_total": 0, "main_eligible": 0,
                                                     "color_total": len(request_data.colors),
                                                     "color_matched": 0}}

    model_id = str(model.get("model_id") or "")
    main_rows = _main_rows(shop_id, model)
    main_found = bool(main_rows)
    white_rows = _white_rows(model_id)
    eligible = [r for r in white_rows if r["_qualification"]["status"] == "eligible"]
    excluded = [r for r in white_rows if r["_qualification"]["status"] != "eligible"]
    eligible.sort(key=lambda r: (_norm(r.get("standard_color")),
                                 str(r.get("display_filename") or r.get("filename") or "").casefold(),
                                 str(r.get("id") or "")))
    selected = eligible[:request_data.need.max_whites]
    color_results, missing_colors = _color_results(
        request_data.colors if request_data.need.color_images else [], eligible, excluded,
        request, model)
    # Multiple exact-path candidates are exposed to the caller without silently
    # selecting one as the product's primary image.
    main = _main_object(main_rows[0], model) if (request_data.need.main and len(main_rows) == 1) else None
    main_candidates = [_main_object(r, model) for r in main_rows] if request_data.need.main and len(main_rows) > 1 else []
    required = (request_data.need.min_whites_with_main if main_found
                else request_data.need.min_whites_without_main)
    blocked_reason = None
    can_satisfy = len(eligible) >= required
    if not main_found and request_data.need.main and not request_data.options.allow_main_substitute:
        can_satisfy = False
        blocked_reason = "该店铺型号没有主图，且不允许白底图替代主图"
    elif not can_satisfy:
        blocked_reason = (f"{'有主图' if main_found else '无主图'}，合格白底图 "
                          f"{len(eligible)}/{required}")
    if request_data.need.color_images and missing_colors and not blocked_reason:
        blocked_reason = "部分颜色没有唯一的合格颜色图"
    if request_data.need.main and len(main_rows) > 1 and not blocked_reason:
        blocked_reason = "该店铺型号有多个主图候选，需调用方选择"
    excluded_out = []
    for row in excluded[:100]:
        q = row["_qualification"]
        excluded_out.append({"image_id": str(row.get("id") or ""),
                             "name": str(row.get("display_filename") or row.get("filename") or ""),
                             "eligibility_status": q["status"],
                             "reason_code": q["reasons"][0]["code"] if q["reasons"] else "unknown",
                             "reason_text": q["reasons"][0]["text"] if q["reasons"] else "无法判定"})
    output_model = _model_output(model)
    return {"request_id": request_id, "matched": True,
            "match": {"level": level, **output_model},
            "main": main, "main_candidates": main_candidates,
            "whites": [_white_object(r, request, model) for r in selected],
            "color_images": color_results,
            "eligibility": {"main_found": main_found,
                            "main_candidate_count": len(main_rows),
                            "eligible_white_count": len(eligible),
                            "required_whites": required,
                            "can_satisfy": can_satisfy,
                            "all_colors_matched": not missing_colors,
                            "blocked_reason": blocked_reason},
            "excluded": excluded_out,
            "excluded_total": len(excluded),
            "model_coverage": {"color_total": len(model.get("colors") or []),
                               "white_total": len(white_rows),
                               "white_eligible": len(eligible),
                               "main_total": len(main_rows), "main_eligible": 0,
                               "color_total": len(request_data.colors),
                               "color_matched": len(request_data.colors) - len(missing_colors)},
            "gallery_version": "phone-A-v1"}


@router.get("/listing/models")
def listing_models(request: Request, category: str = "phone", brand: str = "",
                   q: str = "", model_id: str = "", model_name: str = "",
                   limit: int = Query(default=20, ge=1, le=200),
                   offset: int = Query(default=0, ge=0)):
    if category != "phone":
        raise ListingAPIError(400, "INVALID_ARGUMENT", "首期只支持 category=phone")
    models, _by_id = _gaia_pool()
    candidates = models
    if brand:
        brand_key = brands.brand_key(brand)
        candidates = [m for m in candidates if brand_key in _model_brand_keys(m)]
    if model_id:
        candidates = [m for m in candidates if str(m.get("model_id")) == model_id.strip()]
    if model_name:
        key = _norm(model_name)
        candidates = [m for m in candidates if key in _model_aliases(m)]
    elif q:
        q_key = _norm(q)
        candidates = [m for m in candidates
                      if any(q_key in _norm(v) for v in
                             (m.get("model"), m.get("brand"), m.get("gaia_brand")))]
    candidates.sort(key=lambda m: (str(m.get("brand") or "").casefold(),
                                  str(m.get("model") or "").casefold(),
                                  str(m.get("model_id") or "")))
    page = candidates[offset:offset + limit]
    items = []
    for model in page:
        white_rows = _white_rows(str(model.get("model_id") or ""))
        item = _model_output(model)
        item.update({"color_count": len(model.get("colors") or []),
                     "white_total": len(white_rows),
                     "white_eligible": sum(r["_qualification"]["status"] == "eligible" for r in white_rows),
                     "has_main_by_shop": {},
                     "updated_at": _updated_at(model.get("updated_at")) or _pool_updated_at()})
        items.append(item)
    return {"request_id": getattr(request.state, "request_id", ""),
            "total": len(candidates), "limit": limit, "offset": offset,
            "items": items}


@router.post("/listing/resolve")
def listing_resolve(payload: ResolveRequest, request: Request):
    return _resolve(payload, request)


@router.post("/listing/precheck")
def listing_precheck(payload: PrecheckRequest, request: Request):
    if payload.options.include_unpublished:
        raise ListingAPIError(400, "INVALID_ARGUMENT", "服务 API 只返回业务可用的 A 级图片")
    request_id = payload.request_id or getattr(request.state, "request_id", "")
    items, summary = [], {"total": len(payload.items), "matched": 0,
                         "ok": 0, "blocked": 0, "ambiguous": 0, "not_found": 0}
    for index, item in enumerate(payload.items):
        try:
            data = ResolveRequest(request_id=request_id, shop_id=item.shop_id,
                                  category=item.category, brand=item.brand, model=item.model,
                                  model_id=item.model_id, colors=item.colors,
                                  need=payload.need, options=payload.options)
            resolved = _resolve(data, request)
            if not resolved["matched"]:
                summary["not_found"] += 1
                items.append({"index": index, "matched": False,
                              "error_code": "NOT_FOUND", "model_id": ""})
                continue
            summary["matched"] += 1
            is_ok = bool(resolved["eligibility"]["can_satisfy"] and
                         resolved["eligibility"]["all_colors_matched"] and
                         not resolved["eligibility"]["blocked_reason"])
            summary["ok" if is_ok else "blocked"] += 1
            items.append({"index": index, "matched": True,
                          "model_id": resolved["match"]["model_id"],
                          "model_name": resolved["match"]["model_name"],
                          "main_found": resolved["eligibility"]["main_found"],
                          "eligible_white_count": resolved["eligibility"]["eligible_white_count"],
                          "required_whites": resolved["eligibility"]["required_whites"],
                          "can_satisfy": is_ok,
                          "missing_colors": [x["color"] for x in resolved["color_images"]
                                             if x.get("reason")],
                          "blocked_reason": resolved["eligibility"]["blocked_reason"]})
        except ListingAPIError as exc:
            if exc.code == "MODEL_AMBIGUOUS":
                summary["ambiguous"] += 1
            elif exc.code == "NOT_FOUND":
                summary["not_found"] += 1
            else:
                summary["blocked"] += 1
            items.append({"index": index, "matched": False,
                          "error_code": exc.code, "error_message": exc.message,
                          "details": exc.details})
    return {"request_id": request_id, "summary": summary, "items": items}


def _download_row(image_id: str, request: Request) -> tuple[dict, Path, str, str]:
    # Resolve against both stores instead of depending on undocumented ID prefixes.
    with assets_api.db() as conn:
        white_row = conn.execute("""select * from image_assets where id=? and
            coalesce(category,'phone')='phone' and coalesce(quality_level,'B')='A'
            and coalesce(asset_kind,'white')='white'""", (image_id,)).fetchone()
    if white_row:
        row = dict(white_row)
        source = "white_library"
    else:
        with assets_api.db() as conn:
            row = conn.execute("""select item_id,source_path,name,extension,local_path,
                  sha256,size_bytes,width,height,imported_at
                from douyin_gallery_items where item_id=?""", (image_id,)).fetchone()
        if not row:
            raise ListingAPIError(404, "NOT_FOUND", "图片不存在")
        row = dict(row)
        parts = str(row.get("source_path") or "").split("/")
        shop_id = parts[1] if len(parts) > 1 and parts[0] == "抖音主图" else ""
        _check_shop(request, shop_id)
        source = "douyin_main"
    path = _asset_path(str(row.get("local_path") or ""))
    if path is None:
        raise ListingAPIError(404, "NOT_FOUND", "图片文件不存在")
    if source == "white_library":
        result = _evaluate_white(row)
        if result["status"] != "eligible":
            raise ListingAPIError(422, "IMAGE_NOT_ELIGIBLE", "图片不满足或无法确认抖店白底图条件",
                                  {"reasons": result["reasons"]})
        sha = result["sha256"]
    else:
        try:
            sha = _asset_hash(row, path)
        except OSError:
            raise ListingAPIError(503, "IMAGE_HASH_UNAVAILABLE", "图片 SHA-256 无法读取") from None
    return row, path, sha, source


@router.get("/listing/images/{image_id}/file")
@router.head("/listing/images/{image_id}/file", include_in_schema=False)
def listing_file(image_id: str, request: Request, download: int = Query(default=0, ge=0, le=1),
                 w: int = Query(default=0, ge=0, le=1600)):
    row, path, sha, source = _download_row(image_id, request)
    quoted_etag = f'"{sha}"'
    if w == 0 and request.headers.get("if-none-match", "").strip() == quoted_etag:
        return Response(status_code=304, headers={"ETag": quoted_etag,
                                                  "Cache-Control": "private, max-age=86400"})
    filename = str(row.get("display_filename") or row.get("filename") or row.get("name") or image_id)
    extension = _format(row.get("format") or row.get("extension"))
    media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(
        extension, "application/octet-stream")
    headers = {"ETag": quoted_etag,
               "Cache-Control": "private, max-age=86400",
               "X-Content-Type-Options": "nosniff"}
    if download:
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    if w > 0:
        try:
            from PIL import Image
            import io
            with Image.open(path) as image:
                image = image.convert("RGB")
                image.thumbnail((w, w))
                buffer = io.BytesIO()
                image.save(buffer, "WEBP", quality=82, method=4)
            body = buffer.getvalue()
            return Response(body if request.method != "HEAD" else b"", media_type="image/webp",
                            headers={**headers, "ETag": f'"{hashlib.sha256(body).hexdigest()}"',
                                     "Content-Length": str(len(body))})
        except Exception:
            raise ListingAPIError(500, "INTERNAL_ERROR", "缩略图生成失败") from None
    if request.method == "HEAD":
        headers["Content-Length"] = str(path.stat().st_size)
        return Response(status_code=200, media_type=media, headers=headers)
    headers["Content-Length"] = str(path.stat().st_size)
    return FileResponse(path, media_type=media, headers=headers)
