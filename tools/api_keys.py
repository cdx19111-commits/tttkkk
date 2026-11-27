#!/usr/bin/env python3
"""图库对外接口 API Key 签发工具（唯一入口）。

设计要点：
- 明文钥匙只落在本机 `config/api_keys.local.json`（gitignore），服务器只收 sha256 指纹。
- 每次变更后自动刷新 `API_KEYS.md` 台账（进 Git，供人 review / 供其它会话读取）。
- 服务器文件原子替换，网关按 mtime 热加载，改钥匙不用重启容器。

用法：
    python3 tools/api_keys.py issue --owner 张若彤 --scope read --note "抖音主图对接"
    python3 tools/api_keys.py list
    python3 tools/api_keys.py revoke --owner 张若彤
    python3 tools/api_keys.py sync-doc
    python3 tools/api_keys.py push          # 只同步服务器，不签发
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "config" / "api_keys.local.json"
LEDGER = REPO / "API_KEYS.md"
LOCAL_VIEW = REPO / "API_KEYS.local.md"
PROJECT_LOG = REPO / "项目日志.md"
SSH_HOST = os.environ.get("GALLERY_SSH", "gallery-target")
SERVER_FILE = os.environ.get("GALLERY_API_KEYS_FILE", "/opt/gallery-mvp/runtime/api_keys.json")
SCOPES = ("read", "full")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _load() -> dict:
    if not LOCAL.exists():
        return {"version": 1, "keys": []}
    return json.loads(LOCAL.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    LOCAL.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOCAL.parent.chmod(0o700)
    LOCAL.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    LOCAL.chmod(0o600)


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _server_payload(data: dict) -> dict:
    keys = []
    for item in data["keys"]:
        keys.append({k: item.get(k, "") for k in
                     ("key_id", "sha256", "owner", "scope", "status", "issued_at", "revoked_at", "note")})
    return {"version": 1, "updated_at": _now(), "keys": keys}


def push(data: dict) -> None:
    """原子写入服务器：先写临时文件，再 mv 覆盖，权限 0600。"""
    payload = json.dumps(_server_payload(data), ensure_ascii=False, indent=1)
    target = SERVER_FILE
    script = (
        "set -e; tmp=$(mktemp); cat > \"$tmp\"; chmod 600 \"$tmp\"; "
        f"mkdir -p $(dirname {target}); mv \"$tmp\" {target}; "
        f"chmod 600 {target}; ls -l {target}"
    )
    proc = subprocess.run(["ssh", "-o", "BatchMode=yes", SSH_HOST, script],
                          input=payload, text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"服务器写入失败：{proc.stderr.strip()}")
    print(f"服务器已更新：{target}（{len(data['keys'])} 条记录，仅指纹）")


def sync_doc(data: dict) -> None:
    rows = []
    for item in data["keys"]:
        scope = "只读" if item.get("scope") == "read" else "读写"
        status = "启用" if item.get("status") == "active" else "已撤销"
        rows.append(f"| {item.get('owner','')} | `{item.get('key_id','')}` | {scope} | {status} | "
                    f"{item.get('issued_at','')} | {item.get('note','') or '-'} |")
    body = "\n".join(rows) if rows else "| — | — | — | — | — | 尚未签发任何钥匙 |"
    content = f"""# 对外接口 API Key 台账

> 本文件由 `tools/api_keys.py` 自动生成，**不要手改**。最后更新：{_now()}
> 明文钥匙只在签发人本机的 `config/api_keys.local.json`（gitignore，不入库）；
> 服务器只存 sha256 指纹，位置 `{SERVER_FILE}`。

用法：`/openapi/*` 请求头带 `X-API-Key: <钥匙>`；`只读` 只能调 GET/HEAD，`读写` 才允许写方法。

| 姓名 | 钥匙ID | 权限 | 状态 | 签发时间 | 用途 |
|---|---|---|---|---|---|
{body}
"""
    LEDGER.write_text(content, encoding="utf-8")
    write_local_view(data)
    print(f"台账已刷新：{LEDGER.relative_to(REPO)}（另有本机明文清单 {LOCAL_VIEW.name}）")


def write_local_view(data: dict) -> None:
    """本机明文清单：进 Finder 方便看，但我绝不提交（gitignore）。"""
    rows = []
    for item in data["keys"]:
        scope = "只读" if item.get("scope") == "read" else "读写"
        status = "启用" if item.get("status") == "active" else "已撤销"
        rows.append(f"| {item.get('owner','')} | {scope} | {status} | {item.get('issued_at','')} | "
                    f"{item.get('note','') or '-'} | `{item.get('key','')}` |")
    body = "\n".join(rows) if rows else "| — | — | — | — | — | 尚未签发 |"
    content = f"""# API Key 明文清单（仅本机，绝不提交）

> 本文件由 `tools/api_keys.py` 自动生成，已在 `.gitignore` 中；**不要复制进 API_KEYS.md 或任何提交的文件**。
> 仓库是公开的，钥匙明文一旦提交等于公开。最后更新：{_now()}

| 姓名 | 权限 | 状态 | 签发时间 | 用途 | 钥匙 |
|---|---|---|---|---|---|
{body}

用法示例：

```bash
curl -H "X-API-Key: <上表里的钥匙>" \\
  "http://47.95.20.94:18090/openapi/v1/listing/models?brand=Apple&limit=10"
```
"""
    LOCAL_VIEW.write_text(content, encoding="utf-8")
    LOCAL_VIEW.chmod(0o600)


def append_log(text: str) -> None:
    if not PROJECT_LOG.exists():
        return
    with PROJECT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"\n- {_now()} {text}\n")


def cmd_issue(args) -> int:
    if args.scope not in SCOPES:
        raise SystemExit(f"--scope 只能是 {'/'.join(SCOPES)}")
    data = _load()
    active = [k for k in data["keys"] if k.get("status") == "active"]
    if any(k.get("owner") == args.owner for k in active):
        raise SystemExit(f"{args.owner} 已有启用中的钥匙；先 revoke 再签发，避免一人多把")
    raw = "pk_" + secrets.token_hex(16)
    sha = _sha(raw)
    item = {"key_id": "k_" + sha[:8], "sha256": sha, "owner": args.owner,
            "scope": args.scope, "status": "active", "issued_at": _now(),
            "revoked_at": "", "note": args.note or "", "key": raw}
    data["keys"].append(item)
    _save(data)
    try:
        push(data)
    except SystemExit as exc:
        print(f"⚠️  本地已记录，但服务器未更新：{exc}")
        return 2
    sync_doc(data)
    append_log(f"签发 API Key：{args.owner}（{'只读' if args.scope == 'read' else '读写'}）"
               f"{'，用途：' + args.note if args.note else ''}。key_id={item['key_id']}")
    print("\n===== 转发给对方的钥匙（只显示这一次，也已存在本地文件）=====")
    print(raw)
    print("=" * 52)
    return 0


def cmd_revoke(args) -> int:
    data = _load()
    hit = []
    for item in data["keys"]:
        if item.get("status") != "active":
            continue
        if (args.owner and item.get("owner") == args.owner) or (args.key_id and item.get("key_id") == args.key_id):
            item["status"] = "revoked"
            item["revoked_at"] = _now()
            hit.append(item)
    if not hit:
        raise SystemExit("没有找到启用中的匹配记录")
    _save(data)
    push(data)
    sync_doc(data)
    for item in hit:
        append_log(f"撤销 API Key：{item['owner']}（key_id={item['key_id']}）")
        print(f"已撤销：{item['owner']}  {item['key_id']}")
    return 0


def cmd_list(_args) -> int:
    data = _load()
    if not data["keys"]:
        print("尚未签发任何钥匙。")
        return 0
    print(f"{'姓名':<10}{'钥匙ID':<14}{'权限':<6}{'状态':<8}{'签发时间':<18}用途")
    for item in data["keys"]:
        scope = "只读" if item.get("scope") == "read" else "读写"
        status = "启用" if item.get("status") == "active" else "已撤销"
        print(f"{item.get('owner',''):<10}{item.get('key_id',''):<14}{scope:<6}{status:<8}"
              f"{item.get('issued_at',''):<18}{item.get('note','') or '-'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="图库对外接口 API Key 签发工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_issue = sub.add_parser("issue", help="签发一把新钥匙")
    p_issue.add_argument("--owner", required=True)
    p_issue.add_argument("--scope", default="read", choices=SCOPES)
    p_issue.add_argument("--note", default="")
    p_issue.set_defaults(func=cmd_issue)

    p_revoke = sub.add_parser("revoke", help="撤销钥匙")
    p_revoke.add_argument("--owner", default="")
    p_revoke.add_argument("--key-id", default="")
    p_revoke.set_defaults(func=cmd_revoke)

    p_list = sub.add_parser("list", help="查看台账")
    p_list.set_defaults(func=cmd_list)

    p_sync = sub.add_parser("sync-doc", help="只刷新 API_KEYS.md")
    p_sync.set_defaults(func=lambda a: (sync_doc(_load()), 0)[1])

    p_push = sub.add_parser("push", help="只把本地记录推到服务器")
    p_push.set_defaults(func=lambda a: (push(_load()), 0)[1])

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
