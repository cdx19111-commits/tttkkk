"""Stable read-only storage contracts shared by independently built services. No routes or migrations."""
from __future__ import annotations
import json
import os
import sqlite3
from pathlib import Path
from fastapi import HTTPException

_parents = Path(__file__).resolve().parents
DEFAULT_ROOT = (_parents[3] / "data" / "v03") if len(_parents) > 3 else Path("/data/v03")
ASSET_ROOT = Path(os.getenv("ASSET_ROOT", str(DEFAULT_ROOT)))
ASSET_DB = Path(os.getenv("ASSET_DB", str(ASSET_ROOT / "gallery.db")))
POOL_PATH = Path(os.getenv("GAIA_MODEL_POOL_PATH", "/app/gaia_runtime/gaia_model_pool.json"))

def db() -> sqlite3.Connection:
    if not ASSET_DB.exists():
        raise HTTPException(503, "图片资产库尚未初始化")
    conn = sqlite3.connect(f"file:{ASSET_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


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


def read_gaia_pool(path: Path | None = None) -> dict:
    pool_path = path or POOL_PATH
    if not pool_path.is_file():
        raise HTTPException(503, "Gaia 型号池尚未生成；请先运行每日同步任务")
    try:
        data = json.loads(pool_path.read_text(encoding="utf-8"))
        models = data["models"]
        if not isinstance(models, list) or not models:
            raise ValueError("models 为空或不是数组")
        ids = [str(x.get("model_id", "")).strip() for x in models]
        if any(not x for x in ids) or len(ids) != len(set(ids)):
            raise ValueError("Gaia 型号 ID 缺失或重复")
        return data
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(503, f"Gaia 型号池校验失败：{exc}") from exc


def read_phone_rows(path: Path | None = None) -> list[dict]:
    path = path or ASSET_DB
    if not path.is_file():
        raise HTTPException(503, "图库只读数据库不可用")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        columns = {r[1] for r in conn.execute("pragma table_info(image_assets)")}
        required = {"category", "model_id", "standard_color", "quality_level", "asset_kind"}
        if not required <= columns:
            raise HTTPException(503, "图库缺少白底池必需字段，拒绝按不完整口径统计")
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("select * from image_assets where lower(category)='phone'")]
    finally:
        conn.close()
    return rows

