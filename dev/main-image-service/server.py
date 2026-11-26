"""Local main-image service prototype.

Runs independently behind the gallery gateway during local development.
It owns template/configuration/generation metadata but only writes the local
sandbox (sample/) - never the production gallery.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

SERVICE_ROOT = Path(__file__).resolve().parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from catalog import (ExistingMain, Target, _template_for, brand_group, is_black,
                     load_all_sources, load_shop_targets, orientation_ok, target_key)  # noqa: E402
from engine import compose, cutout_product, foreground_luminance, load_config  # noqa: E402
from sample_config import (BACKUP_ROOT, FONT_PATH, META_DB, OUTPUT_V03, REAL_DB, REAL_V03,
                           SAMPLE_ROOT, SAMPLE_V03, SHOP_ID, SHOP_NAME,
                           TARGET_MODELS, TEMPLATES_CONFIG, TOOL_ROOT)  # noqa: E402
from store import sha256, write_output_atomic  # noqa: E402

DATA_DIR = Path(os.getenv("MAIN_IMAGE_DATA_DIR", SERVICE_ROOT / "service_data"))
SERVICE_DB = Path(os.getenv("MAIN_IMAGE_SERVICE_DB", DATA_DIR / "main_images.db"))
TEMPLATE_DIR = Path(os.getenv("MAIN_IMAGE_TEMPLATE_DIR", DATA_DIR / "templates"))
SERVICE_BACKUP = Path(os.getenv("MAIN_IMAGE_BACKUP_DIR", SERVICE_ROOT / "sample" / "backup_service"))
THUMB_DIR = Path(os.getenv("MAIN_IMAGE_THUMB_DIR", DATA_DIR / "thumbs"))
RUN_LOCK = threading.Lock()
# Local demo: generated files replace the canonical paths in the real local
# snapshot, so the existing Douyin gallery shows the generated image instead
# of a second/parallel item.  Production uses the same idea under v03data.
SERVICE_V03 = OUTPUT_V03
SHOP_NAMES = {
    "129675602": "爱回收官方旗舰店",
    "25595926": "拍拍二手悦玥手机专卖店",
    "636781": "爱回收二手手机旗舰店",
    "7970406": "爱回收严选二手数码",
    "51165770": "爱回收严选二手优品",
    "168976032": "拍拍严选手机优品",
    "5576543": "拍拍严选二手手机",
    "156450040": "拍拍二手数码",
    "208501840": "返航新生二手手机旗舰店",
}
TZ = ZoneInfo("Asia/Shanghai")
AUTO_ENABLED = os.getenv("MAIN_IMAGE_AUTO_ENABLED", "0") == "1"
AUTO_TIME = os.getenv("MAIN_IMAGE_AUTO_TIME", "02:30")
AUTO_SHOPS = [x.strip() for x in os.getenv("MAIN_IMAGE_AUTO_SHOPS", SHOP_ID).split(",") if x.strip()]
GALLERY_WRITE = os.getenv("MAIN_IMAGE_GALLERY_WRITE", "auto")
SCHEDULE_LOCK = threading.Lock()
SCHEDULE_STATE = {"enabled": AUTO_ENABLED, "time": AUTO_TIME, "shops": AUTO_SHOPS,
                  "next_run": "", "last_run": "", "last_status": "idle", "running": False}


def _next_run(now: datetime | None = None) -> datetime:
    now = now or datetime.now(TZ)
    hour, minute = [int(x) for x in AUTO_TIME.split(":", 1)]
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _run_daily(shop_ids: list[str]) -> dict:
    if not RUN_LOCK.acquire(blocking=False):
        SCHEDULE_STATE.update(last_status="busy", last_run=now())
        return {"ok": 0, "failed": 0, "skipped": 0, "busy": True}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-sched"
    counts = {"ok": 0, "failed": 0, "skipped": 0}
    with SCHEDULE_LOCK:
        SCHEDULE_STATE.update(running=True, last_status="running")
    try:
        with db() as conn:
            conn.execute("insert into runs(run_id,scope,started_at,status,counts_json) values (?,?,?,?,?)",
                         (run_id, ",".join(shop_ids), now(), "running", "{}"))
            conn.commit()
            for shop_id in shop_ids:
                for target in targets(shop_id):
                    try:
                        result = generate_one(conn, target, False, run_id)
                        if result.get("_skipped"):
                            counts["skipped"] += 1
                        elif result.get("status") == "ok":
                            counts["ok"] += 1
                        else:
                            counts["failed"] += 1
                    except Exception:
                        counts["failed"] += 1
            conn.execute("update runs set finished_at=?, status='done', counts_json=? where run_id=?",
                         (now(), json.dumps(counts, ensure_ascii=False), run_id))
            conn.commit()
        SCHEDULE_STATE.update(last_status="done", last_run=now(), running=False)
    except Exception as exc:
        SCHEDULE_STATE.update(last_status=f"failed: {type(exc).__name__}", last_run=now(), running=False)
    finally:
        RUN_LOCK.release()
    return counts


def _scheduler_loop() -> None:
    last_run_date = ""
    while True:
        try:
            now = datetime.now(TZ)
            hour, minute = [int(x) for x in AUTO_TIME.split(":", 1)]
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            next_run = due if now < due else due + timedelta(days=1)
            SCHEDULE_STATE["next_run"] = next_run.isoformat(timespec="seconds")
            if AUTO_ENABLED and not SCHEDULE_STATE["running"] \
                    and now >= due and last_run_date != now.date().isoformat():
                last_run_date = now.date().isoformat()
                _run_daily(AUTO_SHOPS)
                SCHEDULE_STATE["next_run"] = _next_run(datetime.now(TZ)).isoformat(timespec="seconds")
        except Exception:
            pass
        time.sleep(30)

from app.service_auth import GatewayRoleMiddleware  # noqa: E402

app = FastAPI(title="主图生成 API（本地）", version="0.1.0",
              docs_url=None, redoc_url=None, openapi_url="/openapi.json")
app.add_middleware(GatewayRoleMiddleware)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SERVICE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript("""
            create table if not exists main_templates(
              id integer primary key autoincrement,
              scope text not null, shop_id text not null default '',
              category text not null, name text, image_path text not null,
              config_json text not null, version integer not null default 1,
              active integer not null default 1, created_at text not null
            );
            create table if not exists model_sources(
              shop_id text not null, model_key text not null,
              asset_id text not null, color text, source_path text, source_sha text,
              selection_reason text, updated_at text not null,
              primary key (shop_id, model_key)
            );
            create table if not exists generated_mains(
              shop_id text not null, model_key text not null,
              model_name text, asset_id text, source_path text, source_sha text,
              template_id integer, template_version integer, template_key text,
              output_path text, output_sha text, old_path text, backup_path text,
              status text, error text, generated_at text, updated_at text,
              primary key (shop_id, model_key)
            );
            create table if not exists runs(
              run_id text primary key, scope text, started_at text, finished_at text,
              status text, counts_json text
            );
            create table if not exists run_items(
              run_id text not null, shop_id text not null, model_key text not null,
              model_name text, status text not null, error text, updated_at text not null,
              primary key (run_id, model_key)
            );
            create index if not exists run_items_latest
              on run_items(shop_id, model_key, updated_at);
            create table if not exists manual_targets(
              shop_id text not null, model_key text not null, model_name text not null,
              brand text, category text, template_key text, created_at text not null,
              primary key (shop_id, model_key)
            );
            create table if not exists settings(
              key text primary key, value text
            );
        """)
        existing = conn.execute("select count(*) from main_templates").fetchone()[0]
        if not existing:
            seed_templates(conn)
        conn.commit()


def default_configs() -> dict:
    cfg = json.loads(TEMPLATES_CONFIG.read_text(encoding="utf-8"))
    return cfg


def seed_templates(conn: sqlite3.Connection) -> None:
    cfg = default_configs()
    default_dir = TEMPLATE_DIR / "default"
    default_dir.mkdir(parents=True, exist_ok=True)
    for key, template in cfg["templates"].items():
        source = TOOL_ROOT / "templates" / f"{key}.png"
        target = default_dir / f"{key}.png"
        if not target.is_file():
            shutil.copy2(source, target)
        template = dict(template)
        template["template_path"] = str(target)
        conn.execute("""insert into main_templates
            (scope, shop_id, category, name, image_path, config_json, version, active, created_at)
            values ('global','',?,?,?,?,1,1,?)""",
            (key, template.get("name", key), str(target),
             json.dumps(template, ensure_ascii=False), now()))


def template_payload(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "scope": row["scope"], "shop_id": row["shop_id"],
            "category": row["category"], "name": row["name"],
            "version": row["version"], "active": bool(row["active"]),
            "image_url": f"/api/v1/main-images/templates/{row['id']}/image",
            "config": json.loads(row["config_json"]),
            "created_at": row["created_at"]}


def resolve_template(conn: sqlite3.Connection, shop_id: str, category: str):
    row = conn.execute("""select * from main_templates
        where active=1 and category=? and scope='shop' and shop_id=?
        order by version desc limit 1""", (category, shop_id)).fetchone()
    if row:
        return row
    return conn.execute("""select * from main_templates
        where active=1 and category=? and scope='global'
        order by version desc limit 1""", (category,)).fetchone()


def _shop_style(shop_id: str) -> str:
    with sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True) as conn:
        depths = [len(str(row[0]).split("/")) for row in conn.execute(
            "select source_path from douyin_gallery_items where source_path like ?",
            (f"抖音主图/{shop_id}/%",))]
    if any(depth >= 6 for depth in depths):
        return "deep"
    if any(depth == 5 for depth in depths):
        return "flat_category"
    return "flat_brand"


def _manual_existing(shop_id: str, model_name: str, brand: str, category: str) -> ExistingMain:
    safe = re.sub(r'[\\/:*?"<>|]', "_", model_name).strip() or model_name
    cat_folder = {"phone": "手机", "tablet": "平板", "watch": "手表", "laptop": "笔记本"}.get(category, "手机")
    brand = brand or "未标品牌"
    style = _shop_style(shop_id)
    if style == "deep":
        rel = f"{cat_folder}/{brand}/{safe}/{safe}.png"
        local = f"douyin_main_images/manual/{shop_id}/{rel}"
    elif style == "flat_category":
        rel = f"{cat_folder}/{brand}/{safe}.png"
        local = f"douyin_main_images/manual/{shop_id}/{rel}"
    else:
        rel = f"{brand}/{safe}.png"
        local = f"douyin_main_images/manual/{shop_id}/{rel}"
    return ExistingMain(f"manual:{shop_id}:{safe}", f"抖音主图/{shop_id}/{rel}",
                        f"{safe}.png", "png", local, 0, 0)


def _gallery_writable() -> bool:
    if GALLERY_WRITE == "1":
        return True
    if GALLERY_WRITE == "0":
        return False
    return os.access(REAL_DB, os.W_OK)


def ensure_manual_gallery_entry(target: Target, output: Path, digest: str, size: int) -> str:
    """Make a manually added model visible in the Douyin gallery tables."""
    if not str(target.existing.item_id).startswith("manual:"):
        return ""
    if not _gallery_writable():
        return "gallery_read_only"
    try:
        conn = sqlite3.connect(REAL_DB, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            item_cols = {row[1] for row in conn.execute("pragma table_info(douyin_gallery_items)")}
            root = conn.execute("select folder_id from douyin_gallery_folders where parent_id='' limit 1").fetchone()
            if not root:
                return "gallery_root_missing"
            rel = str(target.existing.source_path).split("/", 1)[1]
            directory = rel.rsplit("/", 1)[0] if "/" in rel else ""
            parent = root["folder_id"]
            segments = [part for part in directory.split("/") if part]
            for index, segment in enumerate(segments):
                folder_path = "/".join(segments[:index + 1])
                found = conn.execute("select folder_id from douyin_gallery_folders where path=?",
                                     (folder_path,)).fetchone()
                if found:
                    parent = found["folder_id"]
                    continue
                folder_id = "dyf_" + hashlib.sha256(("manual:" + folder_path).encode()).hexdigest()[:24]
                token = "manual_folder_" + hashlib.sha256(folder_path.encode()).hexdigest()[:24]
                conn.execute("""insert into douyin_gallery_folders
                    (folder_id,parent_id,name,path,source_token) values (?,?,?,?,?)""",
                    (folder_id, parent, segment, folder_path, token))
                parent = folder_id
            existing = conn.execute("select item_id from douyin_gallery_items where local_path=?",
                                    (target.existing.local_path,)).fetchone()
            token = "manual_item_" + hashlib.sha256(target.existing.local_path.encode()).hexdigest()[:24]
            common = (parent, target.existing.source_path, target.existing.name,
                      target.existing.extension, target.existing.local_path, digest, size, 1080, 1080)
            if existing:
                if "is_active" in item_cols:
                    conn.execute("""update douyin_gallery_items
                        set folder_id=?, source_path=?, name=?, extension=?, sha256=?,
                            size_bytes=?, width=1080, height=1080, is_active=1
                        where item_id=?""",
                        (parent, target.existing.source_path, target.existing.name,
                         target.existing.extension, digest, size, existing["item_id"]))
                else:
                    conn.execute("""update douyin_gallery_items
                        set folder_id=?, source_path=?, name=?, extension=?, sha256=?,
                            size_bytes=?, width=1080, height=1080
                        where item_id=?""",
                        (parent, target.existing.source_path, target.existing.name,
                         target.existing.extension, digest, size, existing["item_id"]))
            elif "is_active" in item_cols:
                conn.execute("""insert into douyin_gallery_items
                    (item_id,source_token,folder_id,source_path,name,extension,local_path,
                     sha256,size_bytes,width,height,source_url,imported_at,is_active)
                    values (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (target.existing.item_id, token, parent, target.existing.source_path,
                     target.existing.name, target.existing.extension, target.existing.local_path,
                     digest, size, 1080, 1080, "", now()))
            else:
                conn.execute("""insert into douyin_gallery_items
                    (item_id,source_token,folder_id,source_path,name,extension,local_path,
                     sha256,size_bytes,width,height,source_url,imported_at)
                    values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (target.existing.item_id, token, parent, target.existing.source_path,
                     target.existing.name, target.existing.extension, target.existing.local_path,
                     digest, size, 1080, 1080, "", now()))
            conn.commit()
            return ""
        finally:
            conn.close()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def manual_targets(shop_id: str) -> list[Target]:
    all_sources = load_all_sources(REAL_DB)
    with db() as conn:
        rows = conn.execute("select * from manual_targets where shop_id=?", (shop_id,)).fetchall()
    result = []
    for row in rows:
        raw_key = str(row["model_key"] or "")
        model_part = raw_key.split(":", 1)[1] if ":" in raw_key else raw_key
        group = brand_group(row["brand"] or "")
        key = (group, model_part)
        sources = all_sources.get(key)
        if not sources:
            continue
        result.append(Target(model_name=row["model_name"], brand=key[0], group=key[0],
                             key=target_key(key[0], key[1]), template_key=row["template_key"],
                             existing=_manual_existing(shop_id, row["model_name"],
                                                       row["brand"] or "", row["category"] or "phone"),
                             sources=sources, shop_id=shop_id,
                             category=row["category"] or "phone",
                             brand_label=row["brand"] or ""))
    return result


def targets(shop_id: str = SHOP_ID) -> list[Target]:
    auto = load_shop_targets(shop_id, REAL_DB)
    seen = {t.key for t in auto}
    auto.extend(t for t in manual_targets(shop_id) if t.key not in seen)
    return auto


def choose_source(conn: sqlite3.Connection, target: Target):
    saved = conn.execute("""select * from model_sources
        where shop_id=? and model_key=?""", (target.shop_id, target.key)).fetchone()
    ordered = sorted(target.sources,
                     key=lambda item: item.asset_id != (saved["asset_id"] if saved else ""))
    candidates = []
    for source in ordered:
        path = REAL_V03 / source.local_path
        if not path.is_file():
            continue
        try:
            cutout, stats = cutout_product(path)
        except Exception:
            continue
        if stats["largest_ratio"] < 0.02:
            continue
        if not orientation_ok(target.category, stats["cropped_size"]):
            continue
        candidates.append((source, cutout, stats, foreground_luminance(cutout)))
    if not candidates:
        raise RuntimeError("没有方向合格的 A 级源图（需要竖版正面/背面商品图）")
    if saved:
        for source, cutout, stats, _ in candidates:
            if source.asset_id == saved["asset_id"]:
                return source, cutout, stats, "沿用已选源图"
    black = [item for item in candidates if is_black(item[0].color)]
    selected = sorted(black or candidates, key=lambda item: (item[3], item[0].asset_id))[0]
    reason = "黑色优先" if black else "深色优先"
    source, cutout, stats, _ = selected
    conn.execute("""insert into model_sources
        (shop_id, model_key, asset_id, color, source_path, source_sha, selection_reason, updated_at)
        values (?,?,?,?,?,?,?,?)
        on conflict(shop_id,model_key) do update set
          asset_id=excluded.asset_id,color=excluded.color,source_path=excluded.source_path,
          source_sha=excluded.source_sha,selection_reason=excluded.selection_reason,
          updated_at=excluded.updated_at""",
        (target.shop_id, target.key, source.asset_id, source.color, source.local_path,
         sha256(REAL_V03 / source.local_path), reason, now()))
    conn.commit()
    return source, cutout, stats, reason


def output_path(target: Target) -> Path:
    return SERVICE_V03 / target.existing.local_path


def generate_one(conn: sqlite3.Connection, target: Target, force: bool, run_id: str) -> dict:
    template = resolve_template(conn, target.shop_id, target.template_key)
    if not template:
        return {"model_name": target.model_name, "model_key": target.key,
                "status": "no_template", "error": "没有可用模板"}
    previous = conn.execute("""select * from generated_mains
        where shop_id=? and model_key=?""", (target.shop_id, target.key)).fetchone()
    if previous and previous["status"] == "ok" and not force:
        if Path(SERVICE_V03 / previous["output_path"]).is_file() \
                and previous["template_id"] == template["id"] \
                and previous["template_version"] == template["version"]:
            return {**dict(previous), "_skipped": True}
    source, cutout, stats, reason = choose_source(conn, target)
    template_cfg = json.loads(template["config_json"])
    template_cfg["template_path"] = template["image_path"]
    cfg = {"global": {"font_path": FONT_PATH, "bg_mode": "white",
                      "prefer_ai_remove_bg": False},
           "templates": {target.template_key: template_cfg}}
    image = compose(target.template_key, cutout, target.model_name, cfg)
    output = output_path(target)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".tmp")
    image.save(temp, "PNG")
    backup = None
    if output.is_file():
        backup = SERVICE_BACKUP / run_id / target.shop_id / target.existing.local_path
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            backup.unlink()
        shutil.move(str(output), str(backup))
    temp.replace(output)
    output_sha = sha256(output)
    gallery_error = ensure_manual_gallery_entry(target, output, output_sha, output.stat().st_size)
    values = dict(
        shop_id=target.shop_id, model_key=target.key, model_name=target.model_name,
        asset_id=source.asset_id, source_path=str(REAL_V03 / source.local_path),
        source_sha=sha256(REAL_V03 / source.local_path),
        template_id=template["id"], template_version=template["version"],
        template_key=target.template_key, output_path=target.existing.local_path,
        output_sha=output_sha, old_path=str(output) if backup else "",
        backup_path=str(backup) if backup else "", status="ok", error="",
        generated_at=now(), updated_at=now())
    keys = list(values)
    updates = ",".join(f"{k}=excluded.{k}" for k in keys if k not in ("shop_id", "model_key"))
    conn.execute(f"insert into generated_mains ({','.join(keys)}) values ({','.join('?' for _ in keys)}) "
                 f"on conflict(shop_id,model_key) do update set {updates}", [values[k] for k in keys])
    conn.commit()
    if gallery_error:
        values["gallery_error"] = gallery_error
    return values


def item_payload(conn: sqlite3.Connection, target: Target) -> dict:
    generated = conn.execute("""select * from generated_mains
        where shop_id=? and model_key=?""", (target.shop_id, target.key)).fetchone()
    template = resolve_template(conn, target.shop_id, target.template_key)
    source = conn.execute("""select * from model_sources where shop_id=? and model_key=?""",
                          (target.shop_id, target.key)).fetchone()
    status = "missing"
    if generated:
        status = generated["status"]
        if status == "ok":
            if not Path(SERVICE_V03 / generated["output_path"]).is_file():
                status = "missing_file"
            elif template and (generated["template_id"] != template["id"]
                               or generated["template_version"] != template["version"]):
                status = "stale"
    elif not template:
        status = "no_template"
    return {
        "shop_id": target.shop_id, "shop_name": SHOP_NAMES.get(target.shop_id, target.shop_id),
        "model_key": target.key, "model_name": target.model_name,
        "category": target.category, "brand_label": target.brand_label,
        "template_key": target.template_key,
        "template_id": template["id"] if template else None,
        "template_version": template["version"] if template else None,
        "source_color": source["color"] if source else "",
        "source_asset_id": source["asset_id"] if source else "",
        "status": status,
        "generated_at": generated["generated_at"] if generated else "",
        "output_url": (f"/api/v1/main-images/output?shop_id={target.shop_id}&model_key={quote(target.key, safe='')}"
                       if generated and status in {"ok", "stale"} else ""),
        "thumb_url": (f"/api/v1/main-images/output/thumb?shop_id={target.shop_id}&model_key={quote(target.key, safe='')}"
                      if generated and status in {"ok", "stale"} else ""),
        "error": generated["error"] if generated else "",
    }


def migrate_target_keys() -> dict:
    """Move legacy model keys (model only) to brand:model, once per database."""
    with db() as conn:
        row = conn.execute("select value from settings where key='target_key_scheme'").fetchone()
        if row and row["value"] == "brand:model":
            return {"migrated": 0, "unresolved": 0, "skipped": True}

    migrated = 0
    unresolved: list[str] = []

    # Manual mappings are the only table that can be upgraded without targets.
    with db() as conn:
        rows = conn.execute("select rowid, shop_id, model_key, brand from manual_targets").fetchall()
        for row in rows:
            old = str(row["model_key"] or "")
            if ":" in old:
                continue
            new = target_key(brand_group(row["brand"] or ""), old)
            exists = conn.execute("select 1 from manual_targets where shop_id=? and model_key=?",
                                  (row["shop_id"], new)).fetchone()
            if exists:
                conn.execute("delete from manual_targets where rowid=?", (row["rowid"],))
            else:
                conn.execute("update manual_targets set model_key=? where rowid=?",
                             (new, row["rowid"]))
            migrated += 1
        conn.commit()

        shops = {r[0] for r in conn.execute("select distinct shop_id from generated_mains")}
        shops |= {r[0] for r in conn.execute("select distinct shop_id from model_sources")}
        shops |= {r[0] for r in conn.execute("select distinct shop_id from manual_targets")}

    for shop in sorted(x for x in shops if x):
        all_targets = targets(shop)
        by_old: dict[str, list[Target]] = defaultdict(list)
        for target in all_targets:
            by_old[target.key.split(":", 1)[-1]].append(target)

        with db() as conn:
            for row in conn.execute("""select rowid, model_key, model_name from generated_mains
                where shop_id=? and model_key not like '%:%'""", (shop,)).fetchall():
                old = row["model_key"]
                candidates = by_old.get(old, [])
                target = candidates[0] if len(candidates) == 1 else None
                if target is None and candidates:
                    matched = [t for t in candidates if t.model_name == row["model_name"]]
                    target = matched[0] if len(matched) == 1 else None
                if target is None:
                    unresolved.append(f"{shop}/generated/{old}/{row['model_name']}")
                    continue
                exists = conn.execute("select 1 from generated_mains where shop_id=? and model_key=?",
                                      (shop, target.key)).fetchone()
                if exists:
                    conn.execute("delete from generated_mains where rowid=?", (row["rowid"],))
                else:
                    conn.execute("update generated_mains set model_key=? where rowid=?",
                                 (target.key, row["rowid"]))
                migrated += 1

            for row in conn.execute("""select rowid, model_key, asset_id from model_sources
                where shop_id=? and model_key not like '%:%'""", (shop,)).fetchall():
                old = row["model_key"]
                candidates = by_old.get(old, [])
                target = candidates[0] if len(candidates) == 1 else None
                if target is None and candidates:
                    matched = [t for t in candidates
                               if any(s.asset_id == row["asset_id"] for s in t.sources)]
                    target = matched[0] if len(matched) == 1 else None
                if target is None:
                    unresolved.append(f"{shop}/source/{old}/{row['asset_id']}")
                    continue
                exists = conn.execute("select 1 from model_sources where shop_id=? and model_key=?",
                                      (shop, target.key)).fetchone()
                if exists:
                    conn.execute("delete from model_sources where rowid=?", (row["rowid"],))
                else:
                    conn.execute("update model_sources set model_key=? where rowid=?",
                                 (target.key, row["rowid"]))
                migrated += 1
            conn.commit()

    with db() as conn:
        conn.execute("""insert into settings(key,value) values('target_key_scheme','brand:model')
            on conflict(key) do update set value=excluded.value""")
        conn.commit()
    return {"migrated": migrated, "unresolved": len(unresolved), "skipped": False,
            "unresolved_items": unresolved[:20]}


@app.on_event("startup")
def startup() -> None:
    init_db()
    info = migrate_target_keys()
    if info.get("migrated") or info.get("unresolved"):
        print(f"[main-image] model key migration: {info}", flush=True)
    threading.Thread(target=_scheduler_loop, name="main-image-scheduler", daemon=True).start()


@app.get("/healthz", include_in_schema=False)
def healthz():
    try:
        with db() as conn:
            conn.execute("select 1").fetchone()
        return {"ok": True}
    except Exception:
        return JSONResponse({"ok": False}, status_code=503)


@app.get("/api/v1/main-images/shops")
def shops():
    counts: dict[str, int] = {}
    with sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True) as gallery:
        for (path,) in gallery.execute("select source_path from douyin_gallery_items"):
            parts = str(path).split("/")
            if len(parts) >= 3 and parts[0] == "抖音主图":
                counts[parts[1]] = counts.get(parts[1], 0) + 1
    return {"items": [{"shop_id": key, "name": SHOP_NAMES.get(key, key), "images": count}
                      for key, count in sorted(counts.items(), key=lambda x: -x[1])]}


@app.get("/api/v1/main-images/facets")
def facets(shop_id: str = SHOP_ID):
    rows = targets(shop_id)
    categories: dict[str, int] = {}
    brands: dict[str, int] = {}
    models: dict[str, dict] = {}
    for target in rows:
        categories[target.category] = categories.get(target.category, 0) + 1
        brands[target.brand_label] = brands.get(target.brand_label, 0) + 1
        entry = models.setdefault(target.model_name, {"value": target.model_name, "count": 0,
                                                      "category": target.category,
                                                      "brand": target.brand_label})
        entry["count"] += 1
    labels = {"phone": "手机", "tablet": "平板", "watch": "手表", "laptop": "笔记本"}
    return {
        "shop": {"id": shop_id, "name": SHOP_NAMES.get(shop_id, shop_id)},
        "categories": [{"value": key, "label": labels.get(key, key), "count": value}
                       for key, value in sorted(categories.items(), key=lambda x: -x[1])],
        "brands": [{"value": key, "count": value}
                   for key, value in sorted(brands.items(), key=lambda x: -x[1])],
        "models": sorted(models.values(), key=lambda x: (-x["count"], x["value"])),
    }


@app.get("/api/v1/main-images/available-models")
def available_models(shop_id: str = SHOP_ID, q: str = "", limit: int = Query(200, ge=1, le=1000)):
    auto_keys = {t.key for t in load_shop_targets(shop_id, REAL_DB)}
    with db() as conn:
        manual_keys = {row["model_key"] for row in conn.execute(
            "select model_key from manual_targets where shop_id=?", (shop_id,))}
    used = auto_keys | manual_keys
    needle = q.strip().casefold()
    items = []
    for (group, key), sources in load_all_sources(REAL_DB).items():
        if key in used or not sources:
            continue
        source = sources[0]
        haystack = f"{source.model_name} {source.brand} {key}".casefold()
        if needle and needle not in haystack:
            continue
        items.append({"model_key": target_key(group, key), "model_name": source.model_name,
                      "brand": source.brand, "category": source.category,
                      "colors": [x.color for x in sources]})
        if len(items) >= limit:
            break
    return {"items": items, "total": len(items)}


@app.get("/api/v1/main-images/mappings")
def mappings(shop_id: str = SHOP_ID):
    with db() as conn:
        rows = conn.execute("select * from manual_targets where shop_id=? order by model_name",
                            (shop_id,)).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/v1/main-images/mappings")
async def add_mapping(request: Request):
    payload = await request.json()
    shop_id = str(payload.get("shop_id") or SHOP_ID)
    model_key = str(payload.get("model_key") or "").strip()
    brand = str(payload.get("brand") or "").strip()
    if not model_key:
        raise HTTPException(400, "缺少 model_key")
    all_sources = load_all_sources(REAL_DB)
    raw_key = model_key
    model_part = raw_key.split(":", 1)[1] if ":" in raw_key else raw_key
    key = (brand_group(brand), model_part)
    sources = all_sources.get(key)
    if not sources:
        candidates = [(k, v) for k, v in all_sources.items() if k[1] == model_part]
        if len(candidates) != 1:
            raise HTTPException(404, "没有唯一匹配的 A 级白底型号")
        key, sources = candidates[0]
    source = sources[0]
    template_key, category = _template_for(source.category, key[0])
    with db() as conn:
        conn.execute("""insert into manual_targets
            (shop_id, model_key, model_name, brand, category, template_key, created_at)
            values (?,?,?,?,?,?,?)
            on conflict(shop_id,model_key) do update set
              model_name=excluded.model_name, brand=excluded.brand,
              category=excluded.category, template_key=excluded.template_key""",
            (shop_id, target_key(key[0], key[1]), source.model_name, source.brand or brand,
             category, template_key, now()))
        conn.commit()
    return {"ok": True, "shop_id": shop_id, "model_key": target_key(key[0], key[1]),
            "model_name": source.model_name, "template_key": template_key}


@app.post("/api/v1/main-images/mappings/remove")
def remove_mapping(shop_id: str = Query(...), model_key: str = Query(...)):
    with db() as conn:
        conn.execute("delete from manual_targets where shop_id=? and model_key=?",
                     (shop_id, model_key))
        conn.commit()
    return {"ok": True}


@app.get("/api/v1/main-images/schedule")
def schedule():
    return {**SCHEDULE_STATE, "shop_names": {x: SHOP_NAMES.get(x, x) for x in AUTO_SHOPS}}


@app.post("/api/v1/main-images/schedule/run")
def run_schedule(shop_id: str = "", limit: int = 0):
    shops = [shop_id] if shop_id else AUTO_SHOPS
    if limit > 0:
        # Small explicit test batch: only the first N missing/stale targets.
        selected = []
        with db() as conn:
            for shop in shops:
                for target in targets(shop):
                    status = item_payload(conn, target)["status"]
                    if status in {"missing", "stale"}:
                        selected.append((shop, target.key))
                    if len(selected) >= limit:
                        break
                if len(selected) >= limit:
                    break
        counts = {"ok": 0, "failed": 0, "skipped": 0}
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        with db() as conn:
            for shop, key in selected:
                target = next((t for t in targets(shop) if t.key == key), None)
                if not target:
                    continue
                try:
                    result = generate_one(conn, target, False, run_id)
                    counts["ok" if result.get("status") == "ok" else "failed"] += 1
                except Exception:
                    counts["failed"] += 1
        SCHEDULE_STATE.update(last_run=now(), last_status="manual_test")
        return {"run_id": run_id, "counts": counts}
    return _run_daily(shops)


@app.get("/api/v1/main-images/overview")
def overview(shop_id: str = SHOP_ID):
    with db() as conn:
        items = [item_payload(conn, t) for t in targets(shop_id)]
    return {"shop": {"id": shop_id, "name": SHOP_NAMES.get(shop_id, shop_id)}, "items": len(items),
            "generated": sum(x["status"] == "ok" for x in items),
            "missing": sum(x["status"] == "missing" for x in items),
            "stale": sum(x["status"] == "stale" for x in items),
            "failed": sum(x["status"] == "failed" for x in items),
            "no_template": sum(x["status"] == "no_template" for x in items)}


@app.get("/api/v1/main-images/items")
def items(shop_id: str = SHOP_ID, category: str = "", brand: str = "", model: str = "",
          q: str = "", status: str = "", offset: int = Query(0, ge=0),
          limit: int = Query(100, ge=1, le=1000)):
    with db() as conn:
        rows = [item_payload(conn, t) for t in targets(shop_id)]
    if category:
        rows = [x for x in rows if x["category"] == category]
    if brand:
        rows = [x for x in rows if x["brand_label"] == brand]
    if model:
        rows = [x for x in rows if x["model_name"] == model or x["model_key"] == model]
    if q:
        needle = q.casefold()
        rows = [x for x in rows if needle in f"{x['model_name']} {x['model_key']} {x['source_color']}".casefold()]
    if status:
        rows = [x for x in rows if x["status"] == status]
    return {"total": len(rows), "limit": limit, "offset": offset, "items": rows[offset:offset + limit]}


@app.post("/api/v1/main-images/generate")
def generate(payload: Optional[dict] = None):
    payload = payload or {}
    shop_id = str(payload.get("shop_id") or SHOP_ID)
    selected = set(payload.get("model_keys") or [])
    force = bool(payload.get("force"))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results = []
    with db() as conn:
        conn.execute("insert into runs(run_id,scope,started_at,status,counts_json) values (?,?,?,?,?)",
                     (run_id, shop_id, now(), "running", "{}"))
        conn.commit()
        for target in targets(shop_id):
            if selected and target.key not in selected:
                continue
            try:
                result = generate_one(conn, target, force, run_id)
                results.append(result)
            except Exception as exc:
                results.append({"model_name": target.model_name, "model_key": target.key,
                                "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        counts = {"ok": sum(x.get("status") == "ok" and not x.get("_skipped") for x in results),
                  "failed": sum(x.get("status") == "failed" for x in results),
                  "skipped": sum(bool(x.get("_skipped")) for x in results)}
        conn.execute("update runs set finished_at=?, status='done', counts_json=? where run_id=?",
                     (now(), json.dumps(counts, ensure_ascii=False), run_id))
        conn.commit()
    return {"run_id": run_id, "counts": counts, "items": results}


@app.get("/api/v1/main-images/output")
def output(shop_id: str, model_key: str):
    with db() as conn:
        row = conn.execute("select output_path from generated_mains where shop_id=? and model_key=?",
                           (shop_id, model_key)).fetchone()
    if not row:
        raise HTTPException(404, "没有生成记录")
    path = SERVICE_V03 / row["output_path"]
    if not path.is_file():
        raise HTTPException(404, "生成文件不存在")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "private, no-cache"})


@app.get("/api/v1/main-images/output/thumb")
def output_thumb(shop_id: str, model_key: str, size: int = Query(360, ge=80, le=800)):
    with db() as conn:
        row = conn.execute("select output_path from generated_mains where shop_id=? and model_key=?",
                           (shop_id, model_key)).fetchone()
    if not row:
        raise HTTPException(404, "没有生成记录")
    path = SERVICE_V03 / row["output_path"]
    if not path.is_file():
        raise HTTPException(404, "生成文件不存在")
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{shop_id}:{model_key}".encode()).hexdigest()[:10]
    stamp = int(path.stat().st_mtime)
    cache = THUMB_DIR / f"{digest}-{size}-{stamp}.jpg"
    if not cache.is_file():
        from PIL import Image
        with Image.open(path) as image:
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            background.thumbnail((size, size), Image.Resampling.LANCZOS)
            background.save(cache, "JPEG", quality=85, optimize=True)
    return FileResponse(cache, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


def _run_counts(total: int = 0) -> dict:
    return {"total": total, "done": 0, "ok": 0, "failed": 0,
            "skipped": 0, "no_template": 0, "running": 0}


def _execute_run(run_id: str, shop_id: str, keys: list[str], force: bool) -> None:
    """Background worker. The caller already holds RUN_LOCK."""
    counts = _run_counts(len(keys))
    try:
        with db() as conn:
            selected = set(keys)
            work = [t for t in targets(shop_id) if t.key in selected]
            counts["total"] = len(work)
            conn.execute("update runs set counts_json=? where run_id=?",
                         (json.dumps(counts, ensure_ascii=False), run_id))
            conn.commit()
            for target in work:
                counts["running"] = 1
                conn.execute("""update run_items set status='running', updated_at=?
                    where run_id=? and model_key=?""", (now(), run_id, target.key))
                conn.execute("update runs set counts_json=? where run_id=?",
                             (json.dumps(counts, ensure_ascii=False), run_id))
                conn.commit()
                status, error = "ok", ""
                try:
                    result = generate_one(conn, target, force, run_id)
                    if result.get("_skipped"):
                        status = "skipped"
                    elif result.get("status") == "ok":
                        status = "ok"
                    else:
                        status = result.get("status") or "failed"
                        error = result.get("error") or ""
                except Exception as exc:
                    status = "failed"
                    error = f"{type(exc).__name__}: {exc}"
                counts["done"] += 1
                counts["running"] = 0
                if status in counts:
                    counts[status] += 1
                elif status == "ok":
                    counts["ok"] += 1
                else:
                    counts["failed"] += 1
                conn.execute("""update run_items set status=?, error=?, updated_at=?
                    where run_id=? and model_key=?""", (status, error, now(), run_id, target.key))
                conn.execute("update runs set counts_json=? where run_id=?",
                             (json.dumps(counts, ensure_ascii=False), run_id))
                conn.commit()
            conn.execute("update runs set finished_at=?, status='done', counts_json=? where run_id=?",
                         (now(), json.dumps(counts, ensure_ascii=False), run_id))
            conn.commit()
    except Exception as exc:
        counts["running"] = 0
        counts["error"] = f"{type(exc).__name__}: {exc}"
        with db() as conn:
            conn.execute("update runs set finished_at=?, status='failed', counts_json=? where run_id=?",
                         (now(), json.dumps(counts, ensure_ascii=False), run_id))
            conn.commit()
    finally:
        RUN_LOCK.release()


def _run_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    counts = json.loads(row["counts_json"] or "{}")
    errors = [dict(x) for x in conn.execute("""select model_key, model_name, status, error
        from run_items where run_id=? and status in ('failed','no_template')
        order by updated_at desc limit 100""", (row["run_id"],)).fetchall()]
    return {"run_id": row["run_id"], "status": row["status"],
            "started_at": row["started_at"], "finished_at": row["finished_at"],
            **counts, "errors": errors}


@app.post("/api/v1/main-images/runs")
def start_run(payload: Optional[dict] = None):
    payload = payload or {}
    shop_id = str(payload.get("shop_id") or SHOP_ID)
    selected = set(payload.get("model_keys") or [])
    force = bool(payload.get("force"))
    if not RUN_LOCK.acquire(blocking=False):
        raise HTTPException(409, "已有生成任务在运行，请等待完成")
    try:
        all_targets = targets(shop_id)
        work = [t for t in all_targets if not selected or t.key in selected]
        if not work:
            RUN_LOCK.release()
            return {"run_id": "", "total": 0, "status": "done", **(_run_counts())}
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        counts = _run_counts(len(work))
        with db() as conn:
            conn.execute("""insert into runs(run_id,scope,started_at,status,counts_json)
                values (?,?,?,?,?)""",
                (run_id, shop_id, now(), "running", json.dumps(counts, ensure_ascii=False)))
            conn.executemany("""insert or replace into run_items
                (run_id,shop_id,model_key,model_name,status,error,updated_at)
                values (?,?,?,?, 'pending', '', ?)""",
                [(run_id, shop_id, t.key, t.model_name, now()) for t in work])
            conn.commit()
        thread = threading.Thread(target=_execute_run,
                                  args=(run_id, shop_id, [t.key for t in work], force),
                                  name=f"main-image-run-{run_id}", daemon=True)
        thread.start()
        return {"run_id": run_id, "total": len(work), "status": "running", **counts}
    except Exception:
        if RUN_LOCK.locked():
            RUN_LOCK.release()
        raise


@app.get("/api/v1/main-images/runs/current")
def current_run(shop_id: str = SHOP_ID):
    with db() as conn:
        row = conn.execute("""select * from runs where scope=?
            order by started_at desc limit 1""", (shop_id,)).fetchone()
        return {"run": _run_payload(conn, row) if row else None}


@app.get("/api/v1/main-images/runs/{run_id}")
def run_status(run_id: str):
    with db() as conn:
        row = conn.execute("select * from runs where run_id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "生成任务不存在")
        return _run_payload(conn, row)


@app.get("/api/v1/main-images/templates")
def templates(shop_id: str = SHOP_ID, category: str = "", all: bool = False):
    state = "1=1" if all else "active=1"
    with db() as conn:
        if category:
            rows = conn.execute(f"""select * from main_templates where {state} and category=?
                and (scope='global' or (scope='shop' and shop_id=?))
                order by scope desc, version desc""", (category, shop_id)).fetchall()
        else:
            rows = conn.execute(f"""select * from main_templates where {state}
                and (scope='global' or (scope='shop' and shop_id=?))
                order by category, scope desc, version desc""", (shop_id,)).fetchall()
    return {"items": [template_payload(row) for row in rows]}


@app.post("/api/v1/main-images/templates/{template_id}/activate")
def activate_template(template_id: int):
    with db() as conn:
        row = conn.execute("select * from main_templates where id=?", (template_id,)).fetchone()
        if not row:
            raise HTTPException(404, "模板不存在")
        conn.execute("""update main_templates set active=0
            where scope=? and shop_id=? and category=?""",
            (row["scope"], row["shop_id"], row["category"]))
        conn.execute("update main_templates set active=1 where id=?", (template_id,))
        conn.commit()
        new = conn.execute("select * from main_templates where id=?", (template_id,)).fetchone()
    return template_payload(new)


@app.get("/api/v1/main-images/template-sample")
def template_sample(category: str = "ios_phone"):
    with db() as conn:
        row = conn.execute("""select image_path from main_templates
            where scope='global' and category=? and active=1
            order by version desc limit 1""", (category,)).fetchone()
    if not row or not Path(row["image_path"]).is_file():
        raise HTTPException(404, "模板示例不存在")
    return FileResponse(row["image_path"], media_type="image/png",
                        filename=f"{category}-template-sample.png")


@app.get("/api/v1/main-images/templates/{template_id}/image")
def template_image(template_id: int):
    with db() as conn:
        row = conn.execute("select image_path from main_templates where id=?", (template_id,)).fetchone()
    if not row or not Path(row["image_path"]).is_file():
        raise HTTPException(404, "模板图不存在")
    return FileResponse(row["image_path"], media_type="image/png",
                        headers={"Cache-Control": "private, no-cache"})


@app.post("/api/v1/main-images/templates/upload")
async def upload_template(shop_id: str = Form(""), category: str = Form(...),
                          name: str = Form(""), file: UploadFile = File(...),
                          config_json: str = Form("")):
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "模板图不能超过 5MB")
    temp = DATA_DIR / ".template-upload"
    temp.write_bytes(content)
    try:
        from PIL import Image
        with Image.open(temp) as image:
            if image.size != (1080, 1080):
                raise HTTPException(422, "模板必须是 1080×1080")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "模板图无法解码")
    scope = "shop" if shop_id else "global"
    with db() as conn:
        version = conn.execute("""select coalesce(max(version),0)+1 from main_templates
            where scope=? and shop_id=? and category=?""", (scope, shop_id, category)).fetchone()[0]
        base = TEMPLATE_DIR / scope / (shop_id or "default") / category
        base.mkdir(parents=True, exist_ok=True)
        image_path = base / f"v{version}.png"
        shutil.move(str(temp), str(image_path))
        cfg = json.loads(config_json) if config_json else default_configs()["templates"].get(category, {})
        cfg = dict(cfg)
        cfg["template_path"] = str(image_path)
        conn.execute("update main_templates set active=0 where scope=? and shop_id=? and category=?",
                     (scope, shop_id, category))
        cur = conn.execute("""insert into main_templates
            (scope, shop_id, category, name, image_path, config_json, version, active, created_at)
            values (?,?,?,?,?,?,?,1,?)""",
            (scope, shop_id, category, name or category, str(image_path),
             json.dumps(cfg, ensure_ascii=False), version, now()))
        conn.commit()
        row = conn.execute("select * from main_templates where id=?", (cur.lastrowid,)).fetchone()
    return template_payload(row)


@app.post("/api/v1/main-images/templates/{template_id}/config")
async def update_template_config(template_id: int, request: Request):
    payload = await request.json()
    product = payload.get("product_image") or {}
    text = payload.get("product_name") or {}
    with db() as conn:
        row = conn.execute("select * from main_templates where id=?", (template_id,)).fetchone()
        if not row:
            raise HTTPException(404, "模板不存在")
        cfg = json.loads(row["config_json"])
        cfg["product_image"] = {**cfg.get("product_image", {}), **product}
        cfg["product_name"] = {**cfg.get("product_name", {}), **text}
        cfg["template_path"] = row["image_path"]
        version = row["version"] + 1
        conn.execute("""update main_templates set active=0
            where scope=? and shop_id=? and category=?""",
            (row["scope"], row["shop_id"], row["category"]))
        cur = conn.execute("""insert into main_templates
            (scope, shop_id, category, name, image_path, config_json, version, active, created_at)
            values (?,?,?,?,?,?,?,1,?)""",
            (row["scope"], row["shop_id"], row["category"], row["name"],
             row["image_path"], json.dumps(cfg, ensure_ascii=False), version, now()))
        conn.commit()
        new = conn.execute("select * from main_templates where id=?", (cur.lastrowid,)).fetchone()
    return template_payload(new)


@app.get("/api/v1/main-images/preview")
def preview(model_key: str, template_id: Optional[int] = None, shop_id: str = SHOP_ID):
    target = next((t for t in targets(shop_id) if t.key == model_key), None)
    if not target:
        raise HTTPException(404, "型号不存在")
    with db() as conn:
        template = (conn.execute("select * from main_templates where id=?", (template_id,)).fetchone()
                    if template_id else resolve_template(conn, target.shop_id, target.template_key))
        source = conn.execute("select * from model_sources where shop_id=? and model_key=?",
                              (target.shop_id, model_key)).fetchone()
    if not template:
        raise HTTPException(409, "没有可用模板")
    source_path = (REAL_V03 / source["source_path"]) if source else None
    if source_path is None or not source_path.is_file():
        source_asset = target.sources[0]
        source_path = REAL_V03 / source_asset.local_path
    cutout, _ = cutout_product(source_path)
    cfg = json.loads(template["config_json"])
    cfg["template_path"] = template["image_path"]
    image = compose(target.template_key, cutout, target.model_name,
                    {"global": {"font_path": FONT_PATH, "bg_mode": "white",
                                "prefer_ai_remove_bg": False},
                     "templates": {target.template_key: cfg}})
    from io import BytesIO
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return Response(buffer.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "private, no-cache"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8106)
