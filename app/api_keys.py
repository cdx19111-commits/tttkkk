"""对外接口 API Key 校验。

钥匙只以 sha256 指纹形式保存在服务器文件里（默认 /app/runtime/api_keys.json，
即 compose 里 ./runtime:/app/runtime 的挂载点），文件由 `tools/api_keys.py` 原子更新，
网关按 mtime 热加载，改钥匙不需要重启容器。

模式由环境变量 API_KEYS_MODE 控制：
- enforce（默认）：`/openapi/*` 必须带有效 X-API-Key
- log：只记录不拦截（上线观察期用）
- off：完全不校验（排障用）
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from fastapi.responses import JSONResponse

DEFAULT_FILE = "/app/runtime/api_keys.json"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PROTECTED_PREFIX = "/openapi/"

_cache: dict[str, object] = {"mtime": None, "keys": {}}


def fingerprint(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _keys_path() -> Path:
    return Path(os.getenv("API_KEYS_FILE", DEFAULT_FILE))


def _read(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = {}
    for item in data.get("keys", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("status", "active")) != "active":
            continue
        sha = str(item.get("sha256", "")).strip().lower()
        if len(sha) == 64:
            keys[sha] = item
    return keys


def active_keys() -> dict[str, dict]:
    """按文件 mtime 热加载；读取失败时沿用上一份可用缓存。"""
    path = _keys_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] != mtime:
        try:
            _cache["keys"] = _read(path)
            _cache["mtime"] = mtime
        except (OSError, ValueError, TypeError):
            return _cache["keys"]  # 保留上一份可用数据，不因坏文件放行或崩溃
    return _cache["keys"]  # type: ignore[return-value]


def mode() -> str:
    return os.getenv("API_KEYS_MODE", "enforce").strip().lower()


def check(request) -> JSONResponse | None:
    """返回 None 表示放行，否则返回应直接回给客户端的响应。"""
    if mode() == "off":
        return None
    path = request.url.path
    if not path.startswith(PROTECTED_PREFIX):
        return None

    raw = (request.headers.get("x-api-key") or "").strip()
    record = active_keys().get(fingerprint(raw)) if raw else None

    if record is None:
        if mode() == "log":
            print(f"[api_key] unauthorized path={path} has_key={bool(raw)} "
                  f"ua={(request.headers.get('user-agent') or '')[:60]}", flush=True)
            return None
        code = "UNAUTHORIZED" if not raw else "INVALID_KEY"
        message = "缺少 X-API-Key" if not raw else "X-API-Key 无效或已停用"
        return JSONResponse({"code": code, "message": message},
                            status_code=401, headers={"Cache-Control": "no-store"})

    if request.method not in SAFE_METHODS and str(record.get("scope", "read")) != "full":
        return JSONResponse({"code": "FORBIDDEN", "message": "该 API Key 只有只读权限"},
                            status_code=403, headers={"Cache-Control": "no-store"})
    return None
