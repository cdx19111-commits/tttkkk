#!/usr/bin/env python3
"""Add and initialize Douyin gallery active-row columns (dry-run by default).

Usage:
  python migrate_gallery_active.py --gallery-db /path/gallery.db
  python migrate_gallery_active.py --gallery-db /path/gallery.db --execute
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_db(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gallery-db", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.gallery_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    if "douyin_gallery_items" not in tables:
        raise SystemExit("douyin_gallery_items 不存在")
    columns = {row[1] for row in conn.execute("pragma table_info(douyin_gallery_items)")}
    total = conn.execute("select count(*) from douyin_gallery_items").fetchone()[0]
    missing = [name for name in ("is_active", "replaced_by", "archive_batch", "archived_at")
               if name not in columns]
    conn.close()

    report = {
        "gallery_db": str(args.gallery_db),
        "execute": args.execute,
        "total_rows": total,
        "existing_columns": sorted(columns & {"is_active", "replaced_by",
                                              "archive_batch", "archived_at"}),
        "missing_columns": missing,
        "backup": "",
    }
    if not args.execute:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_dir = args.backup_dir or args.gallery_db.parent / "backup"
    backup = backup_dir / f"gallery-active-migration-{stamp}.db"
    backup_db(args.gallery_db, backup)
    report["backup"] = str(backup)

    conn = sqlite3.connect(args.gallery_db, timeout=30)
    try:
        columns = {row[1] for row in conn.execute("pragma table_info(douyin_gallery_items)")}
        for name, ddl in (("is_active", "integer not null default 1"),
                          ("replaced_by", "text"),
                          ("archive_batch", "text"),
                          ("archived_at", "text")):
            if name not in columns:
                conn.execute(f"alter table douyin_gallery_items add column {name} {ddl}")
        conn.execute("update douyin_gallery_items set is_active=1 where is_active is null")
        conn.execute("""create index if not exists idx_douyin_items_active
            on douyin_gallery_items(is_active, folder_id)""")
        conn.commit()
        report["active_rows"] = conn.execute(
            "select count(*) from douyin_gallery_items where coalesce(is_active,1)=1").fetchone()[0]
        report["inactive_rows"] = conn.execute(
            "select count(*) from douyin_gallery_items where coalesce(is_active,1)=0").fetchone()[0]
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
