"""V0.3.4 会话认证：口令校验、服务端 Session、Cookie、限速与 Origin 校验。

设计取舍（与任务书对照）：
- 口令用 PBKDF2-HMAC-SHA256（600k 轮 + 16 字节随机盐）加盐哈希，只存哈希，
  不引入额外依赖，镜像构建不依赖外网 pip；后续可平滑换成 Argon2id（见 verify 的格式分派）。
- 会话是**服务端**记录，落在独立 SQLite（默认 runtime/sessions.db），
  不碰业务库 gallery.db；Cookie 里只有随机令牌，库里只存令牌的 SHA-256。
- Cookie：HttpOnly + Secure + SameSite=Lax + Path=/ + Max-Age=7 天，不设 Domain（仅本站）。
- V0.3.5：会话绑定凭据版本（auth_epoch = 口令哈希的 SHA-256 前 16 位）。
  口令轮换后哈希变化 → 版本变化 → 所有旧会话在下次校验时立即失效并被删除；
  旧版会话行没有该字段（NULL），NULL 不等于任何版本，因此不会绕过校验。
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


def credential_epoch() -> str:
    """当前凭据版本，取口令哈希的 SHA-256 前 16 位。

    - 哈希里已含随机盐，所以每次轮换口令（甚至用同口令重设）都会得到新版本；
    - 版本只由哈希推导，不额外存明文，也不需要人工维护；
    - 哈希未配置时返回空串，此时任何会话都不应通过校验。
    """
    if not PASSWORD_HASH:
        return ""
    return hashlib.sha256(PASSWORD_HASH.encode("utf-8")).hexdigest()[:16]


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
        # auth_epoch 记录建会话时的凭据版本（V0.3.5 新增）
        conn.execute("""create table if not exists sessions(
            sid text primary key,           -- 令牌的 sha256，库里不存原文令牌
            created_at integer not null,
            expires_at integer not null,
            last_seen integer not null,
            ip text, ua text,
            auth_epoch text)""")
        conn.execute("""create table if not exists login_attempts(
            id integer primary key autoincrement,
            ip text not null,
            ts integer not null,
            ok integer not null)""")
        conn.execute("create index if not exists idx_attempts_ip on login_attempts(ip, ts)")
        conn.execute("create index if not exists idx_sessions_exp on sessions(expires_at)")
        _migrate_epoch_column(conn)
    # 会话库仅服务进程可读写（容器内为 root，宿主侧同为 root）
    try:
        os.chmod(SESSION_DB, 0o600)
        for suffix in ("-wal", "-shm"):
            extra = Path(str(SESSION_DB) + suffix)
            if extra.exists():
                os.chmod(extra, 0o600)
    except OSError:
        pass


def _migrate_epoch_column(conn: sqlite3.Connection) -> None:
    """给 V0.3.4 及更早的会话表补 auth_epoch 列。

    刻意**不回填**：旧行保持 NULL，而校验要求与当前版本严格相等，
    因此升级瞬间所有旧会话即失效，不存在"缺字段就跳过校验"的旁路。
    列可为空（alter table 不能加 not null 到已有数据），
    回滚到旧镜像时其 insert 不带该列也能正常写入。
    """
    cols = {row["name"] for row in conn.execute("pragma table_info(sessions)")}
    if "auth_epoch" not in cols:
        conn.execute("alter table sessions add column auth_epoch text")
        legacy = conn.execute(
            "select count(*) c from sessions where auth_epoch is null").fetchone()["c"]
        print(f"[auth] sessions 表已补充 auth_epoch 列；{legacy} 条旧会话将失效", flush=True)



def _sid(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(ip: str = "", ua: str = "") -> str:
    """新建会话，返回明文令牌（仅用于写入 Cookie）。会话绑定当前凭据版本。"""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with _connect() as conn:
        conn.execute("delete from sessions where expires_at <= ?", (now,))
        conn.execute(
            "insert into sessions(sid, created_at, expires_at, last_seen, ip, ua, auth_epoch)"
            " values (?,?,?,?,?,?,?)",
            (_sid(token), now, now + SESSION_TTL, now, ip[:64], ua[:200], credential_epoch()))
    return token


def session_valid(token: str) -> bool:
    if not token:
        return False
    current = credential_epoch()
    if not current:                    # 未配置口令时一律不认会话
        return False
    now = int(time.time())
    with _connect() as conn:
        row = conn.execute("select expires_at, auth_epoch from sessions where sid = ?",
                           (_sid(token),)).fetchone()
        if not row:
            return False
        if row["expires_at"] <= now:
            conn.execute("delete from sessions where sid = ?", (_sid(token),))
            return False
        # 凭据版本不一致（口令已轮换）或旧版行（NULL）→ 立即作废，不留残余
        stored = row["auth_epoch"] or ""
        if not hmac.compare_digest(stored.encode("utf-8"), current.encode("utf-8")):
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


def session_summary() -> dict:
    """按凭据版本汇总会话，供运维核对（不含任何令牌信息）。"""
    current = credential_epoch()
    now = int(time.time())
    with _connect() as conn:
        rows = conn.execute(
            "select auth_epoch, count(*) n, sum(expires_at > ?) alive from sessions"
            " group by auth_epoch", (now,)).fetchall()
    total = sum(r["n"] for r in rows)
    stale = sum(r["n"] for r in rows if (r["auth_epoch"] or "") != current)
    return {"total": total, "current_epoch": current,
            "current": total - stale, "stale": stale,
            "expired": sum(r["n"] - (r["alive"] or 0) for r in rows)}


def purge_stale_sessions() -> int:
    """删除凭据版本不符（含旧版 NULL）的会话，返回删除条数。

    只动 sessions 表，不碰 login_attempts、不碰业务库；用于口令轮换后的收尾清理。
    """
    current = credential_epoch()
    with _connect() as conn:
        if not current:
            return 0
        cur = conn.execute(
            "delete from sessions where auth_epoch is null or auth_epoch <> ?", (current,))
        return cur.rowcount


# ---------- 登录限速（V0.3.4 修复：短窗口 + 温和等待） ----------
#
# 事故背景：旧策略「同一 IP 15 分钟内失败 5 次 → 整个窗口内全部拒绝」，
# 在多人共享出口 IP 的场景下等于让所有同事一起等 10 分钟以上。
#
# 新策略：
#   1) 口令正确 → 一定放行，并清空该 IP 的失败计数（不受他人错误影响）；
#   2) 口令错误 → 401「账号或密码不正确」，首次错误绝不等待；
#   3) 同一 IP 失败达 SOFT_FAILS 次后，每次错误只等十几秒（429 + Retry-After）；
#   4) 同一 IP 失败达 HARD_FAILS 次 → 60 秒硬闸（此阶段不再做哈希计算，省 CPU）；
#   5) 全局失败达 GLOBAL_FAILS 次 → 60 秒整体降速，防分布式撞库。

FAIL_WINDOW = int(os.getenv("LOGIN_WINDOW", "600"))        # 统计窗口 10 分钟
SOFT_FAILS = int(os.getenv("LOGIN_SOFT_FAILS", "5"))       # 达到即进入"每次要等"
SOFT_WAIT = int(os.getenv("LOGIN_SOFT_WAIT", "15"))        # 单次等待上限（秒）
HARD_FAILS = int(os.getenv("LOGIN_HARD_FAILS", "30"))      # 达到即硬闸
HARD_WAIT = int(os.getenv("LOGIN_HARD_WAIT", "60"))        # 硬闸时长（秒）
GLOBAL_FAILS = int(os.getenv("LOGIN_GLOBAL_FAILS", "200"))  # 全局熔断阈值
PROBE_GAP = int(os.getenv("LOGIN_PROBE_GAP", "5"))         # 硬闸期间每次校验的最小间隔（秒）


def _prune_attempts(conn: sqlite3.Connection, now: int) -> None:
    conn.execute("delete from login_attempts where ts < ?", (now - FAIL_WINDOW * 6,))


MARK_GATE = 2      # 硬闸开始时间标记
MARK_PROBE = 3     # 硬闸期间最近一次被放行校验的时间标记


def _add_marker(conn: sqlite3.Connection, ip: str, kind: int, ts: int) -> None:
    conn.execute("insert into login_attempts(ip, ts, ok) values (?,?,?)", (ip[:64], ts, kind))


def _last_marker(conn: sqlite3.Connection, ip: str, kind: int, since: int) -> int:
    row = conn.execute(
        "select max(ts) t from login_attempts where ip = ? and ok = ? and ts > ?",
        (ip[:64], kind, since)).fetchone()
    return (row["t"] or 0) if row else 0


def _ip_fails(conn: sqlite3.Connection, ip: str, now: int) -> tuple[int, int]:
    row = conn.execute(
        "select count(*) c, min(ts) t from login_attempts where ip = ? and ok = 0 and ts > ?",
        (ip, now - FAIL_WINDOW)).fetchone()
    return row["c"], (row["t"] or now)


def soft_delay(ip: str) -> int:
    """错误口令在软阈值之后需要等待的秒数；0 表示无需等待。"""
    now = int(time.time())
    with _connect() as conn:
        count, first = _ip_fails(conn, ip, now)
    if count <= SOFT_FAILS:      # 前 SOFT_FAILS 次只是普通「密码错误」，不要求等待
        return 0
    remain = max(1, FAIL_WINDOW - (now - first))
    return max(1, min(SOFT_WAIT, remain))


def gate_decision(ip: str) -> int:
    """登录前的闸门判定，返回需要等待的秒数（0 表示放行本次校验）。

    设计要点（V0.3.4 收尾修复）：
    - 硬闸不再是"整段时间内谁都不许试"，而是**给每个 IP 每 PROBE_GAP 秒一次校验机会**：
      知道口令的人最多等 PROBE_GAP 秒就能进来，撞库速度却被压到约 12 次/分钟；
    - 触发硬闸的那一次请求直接放行校验（口令正确就正常登录）；
    - 只有全局限速（分布式撞库熔断）才不做校验——它是 CPU 保护的最后一道闸。
    """
    now = int(time.time())
    blocked = global_block()
    if blocked:
        return blocked

    with _connect() as conn:
        gate = _last_marker(conn, ip, MARK_GATE, now - HARD_WAIT)
        if not gate:
            count, _ = _ip_fails(conn, ip, now)
            if count < HARD_FAILS:
                return 0
            # 触发硬闸：清零失败计数、打时间标记，并放行本次校验
            conn.execute("delete from login_attempts where ip = ? and ok = 0", (ip[:64],))
            _add_marker(conn, ip, MARK_GATE, now)
            _add_marker(conn, ip, MARK_PROBE, now)
            return 0

        last = _last_marker(conn, ip, MARK_PROBE, now - HARD_WAIT)
        if now - last >= PROBE_GAP:
            _add_marker(conn, ip, MARK_PROBE, now)
            return 0
        return max(1, PROBE_GAP - (now - last))


def global_block() -> int:
    """全局熔断：只在高频撞库时短暂降速，同样到点自动解除。"""
    now = int(time.time())
    with _connect() as conn:
        mark = conn.execute(
            "select max(ts) t from login_attempts where ip = '__global__' and ok = 2 and ts > ?",
            (now - HARD_WAIT,)).fetchone()
        if mark and mark["t"]:
            return max(1, HARD_WAIT - (now - mark["t"]))
        total = conn.execute(
            "select count(*) c from login_attempts where ok = 0 and ts > ?",
            (now - FAIL_WINDOW,)).fetchone()["c"]
        if total >= GLOBAL_FAILS:
            # 触发即清零窗口内的失败计数，否则标记过期后会立刻再次触发、变成永久封锁
            conn.execute("delete from login_attempts where ok = 0 and ts > ?", (now - FAIL_WINDOW,))
            conn.execute("insert into login_attempts(ip, ts, ok) values ('__global__',?,2)", (now,))
            return HARD_WAIT
    return 0


def record_failure(ip: str) -> None:
    now = int(time.time())
    with _connect() as conn:
        _prune_attempts(conn, now)
        conn.execute("insert into login_attempts(ip, ts, ok) values (?,?,0)", (ip[:64], now))


def clear_failures(ip: str) -> None:
    """登录成功后清空该 IP 的失败计数，避免同事被同网段的错误连累。"""
    now = int(time.time())
    with _connect() as conn:
        # 连硬闸/探测标记一起清，避免登录成功后仍被旧的闸门状态影响
        conn.execute("delete from login_attempts where ip = ? and ok in (0, 2, 3)", (ip[:64],))
        conn.execute("insert into login_attempts(ip, ts, ok) values (?,?,1)", (ip[:64], now))
