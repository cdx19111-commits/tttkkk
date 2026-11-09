"""V0.3.4 会话认证：口令校验、服务端 Session、Cookie、限速与 Origin 校验。

设计取舍（与任务书对照）：
- 口令用 PBKDF2-HMAC-SHA256（600k 轮 + 16 字节随机盐）加盐哈希，只存哈希，
  不引入额外依赖，镜像构建不依赖外网 pip；后续可平滑换成 Argon2id（见 verify 的格式分派）。
- 会话是**服务端**记录，落在独立 SQLite（默认 runtime/sessions.db），
  不碰业务库 gallery.db；Cookie 里只有随机令牌，库里只存令牌的 SHA-256。
- Cookie：HttpOnly + Secure + SameSite=Lax + Path=/ + Max-Age=7 天，不设 Domain（仅本站）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path

# ---------- 配置（全部来自环境变量，代码中无任何口令/密钥） ----------

LOGIN_USER = os.getenv("GALLERY_USER", "爱回收")
PASSWORD_HASH = os.getenv("GALLERY_PASSWORD_HASH", "").strip()

COOKIE_NAME = os.getenv("SESSION_COOKIE", "gallery_session")
SESSION_TTL = int(os.getenv("SESSION_TTL", "604800"))          # 7 天
COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "1") != "0"  # 生产恒为 True，仅本地联调可关

SESSION_DB = Path(os.getenv("SESSION_DB", "runtime/sessions.db"))

FAIL_LIMIT = int(os.getenv("LOGIN_FAIL_LIMIT", "5"))     # 同一 IP 在窗口内的失败次数上限
FAIL_WINDOW = int(os.getenv("LOGIN_FAIL_WINDOW", "900"))  # 15 分钟
GLOBAL_FAIL_LIMIT = int(os.getenv("LOGIN_FAIL_GLOBAL", "60"))

PBKDF2_ITER = 600_000
PBKDF2_PREFIX = "pbkdf2_sha256"

# 口令未配置时用于等价耗时的占位哈希，避免通过响应时间区分「账号不存在」与「口令错误」
_DUMMY_HASH = "{}:{}:{}:{}".format(
    PBKDF2_PREFIX, PBKDF2_ITER,
    base64.b64encode(b"0" * 16).decode(),
    base64.b64encode(hashlib.pbkdf2_hmac("sha256", b"x", b"0" * 16, PBKDF2_ITER)).decode())


# ---------- 口令哈希 ----------

def hash_password(password: str) -> str:
    """生成 `pbkdf2_sha256:<迭代>:<b64盐>:<b64摘要>`；盐每次随机。

    用冒号而不是 `$` 分隔：`$` 在 docker compose 的 .env 解析、shell 双引号里
    都会被当成变量插值，实测会把哈希截断（本机与线上都踩过）。
    """
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)
    return "{}:{}:{}:{}".format(PBKDF2_PREFIX, PBKDF2_ITER,
                                base64.b64encode(salt).decode(),
                                base64.b64encode(dk).decode())


def verify_password(password: str, stored: str = "") -> bool:
    """校验口令。stored 为空（未配置）时仍走一次等价耗时的哈希，但恒返回 False。"""
    configured = bool(stored)
    # 兼容旧格式（$ 分隔）与新格式（: 分隔）
    target = (stored or _DUMMY_HASH).replace(":", "$")
    try:
        algo, iters, salt_b64, hash_b64 = target.split("$")
        if algo != PBKDF2_PREFIX:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected) and configured
    except (ValueError, TypeError):
        return False


def hash_configured() -> bool:
    return bool(PASSWORD_HASH)


# ---------- 会话存储 ----------

def _connect() -> sqlite3.Connection:
    SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SESSION_DB, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma synchronous=normal")
    return conn


def init_store() -> None:
    with _connect() as conn:
        conn.execute("""create table if not exists sessions(
            sid text primary key,           -- 令牌的 sha256，库里不存原文令牌
            created_at integer not null,
            expires_at integer not null,
            last_seen integer not null,
            ip text, ua text)""")
        conn.execute("""create table if not exists login_attempts(
            id integer primary key autoincrement,
            ip text not null,
            ts integer not null,
            ok integer not null)""")
        conn.execute("create index if not exists idx_attempts_ip on login_attempts(ip, ts)")
        conn.execute("create index if not exists idx_sessions_exp on sessions(expires_at)")
    # 会话库仅服务进程可读写（容器内为 root，宿主侧同为 root）
    try:
        os.chmod(SESSION_DB, 0o600)
        for suffix in ("-wal", "-shm"):
            extra = Path(str(SESSION_DB) + suffix)
            if extra.exists():
                os.chmod(extra, 0o600)
    except OSError:
        pass


def _sid(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(ip: str = "", ua: str = "") -> str:
    """新建会话，返回明文令牌（仅用于写入 Cookie）。"""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with _connect() as conn:
        conn.execute("delete from sessions where expires_at <= ?", (now,))
        conn.execute(
            "insert into sessions(sid, created_at, expires_at, last_seen, ip, ua) values (?,?,?,?,?,?)",
            (_sid(token), now, now + SESSION_TTL, now, ip[:64], ua[:200]))
    return token


def session_valid(token: str) -> bool:
    if not token:
        return False
    now = int(time.time())
    with _connect() as conn:
        row = conn.execute("select expires_at from sessions where sid = ?", (_sid(token),)).fetchone()
        if not row:
            return False
        if row["expires_at"] <= now:
            conn.execute("delete from sessions where sid = ?", (_sid(token),))
            return False
        conn.execute("update sessions set last_seen = ? where sid = ?", (now, _sid(token)))
    return True


def revoke_session(token: str) -> None:
    if not token:
        return
    with _connect() as conn:
        conn.execute("delete from sessions where sid = ?", (_sid(token),))


def count_sessions() -> int:
    with _connect() as conn:
        return conn.execute("select count(*) from sessions").fetchone()[0]


# ---------- 登录限速 ----------

def _prune_attempts(conn: sqlite3.Connection, now: int) -> None:
    conn.execute("delete from login_attempts where ts < ?", (now - FAIL_WINDOW * 4,))


def rate_limited(ip: str) -> int:
    """返回 0 表示未限速；否则返回建议的 Retry-After 秒数。"""
    now = int(time.time())
    with _connect() as conn:
        _prune_attempts(conn, now)
        row = conn.execute(
            "select count(*) c, min(ts) t from login_attempts where ip = ? and ok = 0 and ts > ?",
            (ip, now - FAIL_WINDOW)).fetchone()
        if row["c"] >= FAIL_LIMIT:
            return max(30, FAIL_WINDOW - (now - row["t"]))
        total = conn.execute(
            "select count(*) c from login_attempts where ok = 0 and ts > ?",
            (now - FAIL_WINDOW,)).fetchone()["c"]
        if total >= GLOBAL_FAIL_LIMIT:
            return 300          # 全局熔断：明显有分布式撞库时整体降速
    return 0


def record_attempt(ip: str, ok: bool) -> None:
    now = int(time.time())
    with _connect() as conn:
        conn.execute("insert into login_attempts(ip, ts, ok) values (?,?,?)",
                     (ip[:64], now, 1 if ok else 0))
        if ok:
            # 登录成功即清空该 IP 的失败计数，避免正常用户被自己之前的错误拖累
            conn.execute("delete from login_attempts where ip = ? and ok = 0", (ip[:64],))
