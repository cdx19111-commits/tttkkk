"""对外接口 API Key 校验的单元与网关集成测试。"""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app import api_keys, main
from app.main import app


def _write_keys(tmp_path, records):
    path = tmp_path / "api_keys.json"
    path.write_text(json.dumps({"version": 1, "keys": records}, ensure_ascii=False), encoding="utf-8")
    return path


def _rec(raw, scope="read", status="active"):
    return {"key_id": "k_test", "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "owner": "测试", "scope": scope, "status": status,
            "issued_at": "2026-11-27 18:00", "revoked_at": "", "note": "pytest"}


@pytest.fixture
def keys_env(tmp_path, monkeypatch):
    monkeypatch.setattr(main.storage_contract, "quality_schema_ok", lambda: (True, ""))

    def setup(records, mode="enforce"):
        path = _write_keys(tmp_path, records)
        monkeypatch.setenv("API_KEYS_FILE", str(path))
        monkeypatch.setenv("API_KEYS_MODE", mode)
        monkeypatch.setattr(api_keys, "_cache", {"mtime": None, "keys": {}})
        return path
    yield setup
    monkeypatch.setattr(api_keys, "_cache", {"mtime": None, "keys": {}})


def test_fingerprint_is_sha256():
    assert api_keys.fingerprint("pk_abc") == hashlib.sha256(b"pk_abc").hexdigest()


def test_openapi_requires_key(keys_env):
    keys_env([_rec("pk_good")])
    client = TestClient(app)
    assert client.get("/openapi/v1/nope").status_code == 401
    assert client.get("/openapi/v1/nope", headers={"X-API-Key": "pk_bad"}).status_code == 401


def test_valid_key_passes_through_to_router(keys_env):
    keys_env([_rec("pk_good")])
    client = TestClient(app)
    # 路径不存在 → 404 说明中间件已放行（未走到 401/403）
    assert client.get("/openapi/v1/nope", headers={"X-API-Key": "pk_good"}).status_code not in (401, 403)


def test_read_scope_blocks_write_methods(keys_env):
    keys_env([_rec("pk_read", scope="read"), _rec("pk_full", scope="full")])
    client = TestClient(app)
    assert client.post("/openapi/v1/nope", headers={"X-API-Key": "pk_read"}).status_code == 403
    assert client.post("/openapi/v1/nope", headers={"X-API-Key": "pk_full"}).status_code not in (401, 403)


def test_revoked_key_rejected(keys_env):
    keys_env([_rec("pk_old", status="revoked")])
    client = TestClient(app)
    assert client.get("/openapi/v1/nope", headers={"X-API-Key": "pk_old"}).status_code == 401


def test_log_mode_does_not_block(keys_env):
    keys_env([], mode="log")
    client = TestClient(app)
    assert client.get("/openapi/v1/nope").status_code not in (401, 403)


def test_page_api_is_not_protected(keys_env):
    keys_env([_rec("pk_good")])
    client = TestClient(app)
    # 页面自用接口不要求 key
    assert client.get("/api/v1/nope").status_code not in (401, 403)


def test_hot_reload_by_mtime(tmp_path, monkeypatch):
    path = _write_keys(tmp_path, [])
    monkeypatch.setenv("API_KEYS_FILE", str(path))
    monkeypatch.setenv("API_KEYS_MODE", "enforce")
    monkeypatch.setattr(api_keys, "_cache", {"mtime": None, "keys": {}})
    assert api_keys.active_keys() == {}
    _write_keys(tmp_path, [_rec("pk_new")])
    import os
    os.utime(path, (path.stat().st_atime + 5, path.stat().st_mtime + 5))
    assert api_keys.fingerprint("pk_new") in api_keys.active_keys()


def test_broken_file_keeps_last_good_cache(tmp_path, monkeypatch):
    path = _write_keys(tmp_path, [_rec("pk_keep")])
    monkeypatch.setenv("API_KEYS_FILE", str(path))
    monkeypatch.setattr(api_keys, "_cache", {"mtime": None, "keys": {}})
    assert api_keys.active_keys()
    path.write_text("{not json", encoding="utf-8")
    import os
    os.utime(path, (path.stat().st_atime + 9, path.stat().st_mtime + 9))
    # 坏文件不应清空已加载的钥匙，也不应抛异常
    assert api_keys.fingerprint("pk_keep") in api_keys.active_keys()
