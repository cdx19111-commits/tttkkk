"""Ordered model parameters shared as a stable read-only data contract."""
from __future__ import annotations
import json
from pathlib import Path
from fastapi import HTTPException
MODEL_PARAMS_PATH = Path(__file__).resolve().parent / "static" / "model_params.json"
_MODEL_PARAMS_CACHE: dict | None = None

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

