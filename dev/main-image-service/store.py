"""Tiny metadata store and backup helpers for the local sample."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sample_config import BACKUP_ROOT, META_DB, SAMPLE_ROOT, SAMPLE_V03, SHOP_ID


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect() -> sqlite3.Connection:
    SAMPLE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(META_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        create table if not exists model_sources(
            shop_id text not null, model_key text not null,
            asset_id text not null, color text, source_path text, source_sha text,
            selection_reason text, updated_at text not null,
            primary key (shop_id, model_key)
        );
        create table if not exists generated_mains(
            shop_id text not null, model_key text not null,
            model_name text, asset_id text, source_path text, source_sha text,
            template_key text, template_version integer,
            output_path text, output_sha text, old_path text, backup_path text,
            status text, error text, generated_at text, updated_at text,
            primary key (shop_id, model_key)
        );
        create table if not exists runs(
            run_id text primary key, started_at text, finished_at text,
            status text, counts_json text
        );
    """)
    conn.commit()
    return conn


def existing_path(relative: str) -> Path:
    return SAMPLE_V03 / relative


def backup_existing(relative: str, run_id: str) -> tuple[Path, Path] | None:
    source = existing_path(relative)
    if not source.is_file():
        return None
    target = BACKUP_ROOT / run_id / SHOP_ID / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return source, target


def write_output_atomic(output_path: Path, image, quality: int = 95) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_name(output_path.name + ".tmp")
    if output_path.suffix.lower() in {".jpg", ".jpeg"}:
        from PIL import Image
        rgb = Image.new("RGB", image.size, (255, 255, 255))
        rgb.paste(image, mask=image.split()[-1] if image.mode == "RGBA" else None)
        rgb.save(temp, "JPEG", quality=quality)
    else:
        image.save(temp, "PNG")
    os.replace(temp, output_path)


def upsert_source(conn: sqlite3.Connection, shop_id: str, model_key: str,
                  asset_id: str, color: str, source_path: str, source_sha: str,
                  reason: str) -> None:
    conn.execute("""insert into model_sources
        (shop_id, model_key, asset_id, color, source_path, source_sha,
         selection_reason, updated_at)
        values (?,?,?,?,?,?,?,?)
        on conflict(shop_id, model_key) do update set
          asset_id=excluded.asset_id, color=excluded.color,
          source_path=excluded.source_path, source_sha=excluded.source_sha,
          selection_reason=excluded.selection_reason, updated_at=excluded.updated_at""",
        (shop_id, model_key, asset_id, color, source_path, source_sha, reason, now()))
    conn.commit()


def get_source(conn: sqlite3.Connection, shop_id: str, model_key: str):
    return conn.execute(
        "select * from model_sources where shop_id=? and model_key=?",
        (shop_id, model_key)).fetchone()


def get_generated(conn: sqlite3.Connection, shop_id: str, model_key: str):
    return conn.execute(
        "select * from generated_mains where shop_id=? and model_key=?",
        (shop_id, model_key)).fetchone()


def upsert_generated(conn: sqlite3.Connection, **values) -> None:
    keys = list(values)
    updates = ",".join(f"{k}=excluded.{k}" for k in keys if k not in ("shop_id", "model_key"))
    conn.execute(
        f"insert into generated_mains ({','.join(keys)}) values ({','.join('?' for _ in keys)}) "
        f"on conflict(shop_id, model_key) do update set {updates}",
        [values[k] for k in keys])
    conn.commit()
