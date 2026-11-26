#!/usr/bin/env python3
"""Archive old Douyin main images and soft-hide their DB rows.

This is the apply counterpart of archive_plan.py.  It is deliberately explicit:
without --execute it only prints the planned operations.

Safety:
  * never deletes an image; old files are moved to an archive directory;
  * backs up the gallery DB before any write;
  * writes a receipt with every moved file and row change;
  * only processes models that have a generated main image.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from archive_plan import (DEFAULT_ASSET_ROOT, DEFAULT_GALLERY, DEFAULT_GENERATED,
                          SHOP_NAMES, choose_canonical, item_key, load_gallery,
                          load_generated, sha256)
from catalog import _parse_item, brand_group, model_key, target_key


ROOT = Path(__file__).resolve().parent
DEFAULT_GENERATED_SOURCE = ROOT / "sample" / "v03"
DEFAULT_ARCHIVE_ROOT = ROOT / "archive_rehearsal" / "backup"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("pragma table_info(douyin_gallery_items)")}
    for name, ddl in (
        ("is_active", "integer not null default 1"),
        ("replaced_by", "text"),
        ("archive_batch", "text"),
        ("archived_at", "text"),
    ):
        if name not in cols:
            conn.execute(f"alter table douyin_gallery_items add column {name} {ddl}")
    conn.execute("update douyin_gallery_items set is_active=1 where is_active is null")


def backup_db(db_path: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def move_to_archive(path: Path, asset_root: Path, archive_root: Path,
                    batch: str, shop: str, local_path: str) -> str | None:
    if not path.is_file():
        return None
    rel = Path(local_path)
    target = archive_root / batch / shop / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.with_name(target.name + ".duplicate")
    shutil.move(str(path), str(target))
    return str(target)


def build_operations(gallery_db: Path, generated_db: Path, asset_root: Path,
                     generated_source_root: Path, only_with_old: bool) -> list[dict]:
    gallery = load_gallery(gallery_db)
    generated = load_generated(generated_db)
    operations = []
    for item in generated:
        shop = item["shop_id"]
        key = item["model_key"]
        output_path = str(item.get("output_path") or "")
        source = generated_source_root / output_path
        candidates = gallery.get((shop, key), [])
        canonical = choose_canonical(candidates, item)
        old_files = []
        for row in candidates:
            path = asset_root / str(row["local_path"] or "")
            if not path.is_file():
                continue
            digest = sha256(path)
            if digest != str(item.get("output_sha") or ""):
                old_files.append((row, path, digest))
        if only_with_old and not old_files:
            continue
        if not source.is_file():
            operations.append({"shop_id": shop, "model_key": key, "action": "skip",
                               "reason": "generated_file_missing", "output_path": output_path})
            continue
        if not canonical:
            operations.append({"shop_id": shop, "model_key": key, "action": "skip",
                               "reason": "no_existing_row", "output_path": output_path})
            continue
        canonical_path = canonical["local_path"]
        target = asset_root / canonical_path
        operations.append({
            "shop_id": shop, "model_key": key, "model_name": item["model_name"],
            "canonical_item_id": canonical["item_id"], "canonical_path": canonical_path,
            "generated_source": str(source), "generated_sha": sha256(source),
            "old_files": [{"item_id": r["item_id"], "local_path": r["local_path"],
                           "sha256": d} for r, _p, d in old_files],
            "extra_rows": [r["item_id"] for r in candidates if r["item_id"] != canonical["item_id"]],
            "target_exists": target.is_file(),
            "action": "archive_and_replace",
        })
    return operations


def apply_operations(gallery_db: Path, asset_root: Path, archive_root: Path,
                     batch: str, operations: list[dict], execute: bool) -> dict:
    receipt = {"batch": batch, "started_at": now(), "executed": execute,
               "gallery_db": str(gallery_db), "asset_root": str(asset_root),
               "archive_root": str(archive_root), "models": [], "summary": {}}
    if not execute:
        receipt["summary"] = {"planned": len(operations),
                              "archive_files": sum(len(o.get("old_files", [])) for o in operations),
                              "skipped": sum(o.get("action") == "skip" for o in operations)}
        return receipt

    backup = archive_root / batch / "gallery_pre.db"
    backup_db(gallery_db, backup)
    conn = sqlite3.connect(gallery_db)
    conn.row_factory = sqlite3.Row
    ensure_columns(conn)
    conn.commit()
    moved = 0
    hidden = 0
    replaced = 0
    for op in operations:
        if op.get("action") == "skip":
            receipt["models"].append(op)
            continue
        shop = op["shop_id"]
        for old in op["old_files"]:
            path = asset_root / old["local_path"]
            archived = move_to_archive(path, asset_root, archive_root, batch,
                                       shop, old["local_path"])
            if archived:
                conn.execute("""update douyin_gallery_items
                    set is_active=0, replaced_by=?, archive_batch=?, archived_at=?
                    where item_id=?""",
                    (op["model_key"], batch, now(), old["item_id"]))
                old["archive_path"] = archived
                moved += 1
                if old["item_id"] != op["canonical_item_id"]:
                    hidden += 1
        target = asset_root / op["canonical_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        shutil.copy2(op["generated_source"], temp)
        temp.replace(target)
        digest = sha256(target)
        size = target.stat().st_size
        conn.execute("""update douyin_gallery_items
            set is_active=1, replaced_by=?, archive_batch=?, archived_at=NULL,
                width=1080, height=1080, size_bytes=?, sha256=?, extension='png'
            where item_id=?""",
            (op["model_key"], batch, size, digest, op["canonical_item_id"]))
        for item_id in op.get("extra_rows", []):
            conn.execute("""update douyin_gallery_items
                set is_active=0, replaced_by=?, archive_batch=?, archived_at=?
                where item_id=?""", (op["model_key"], batch, now(), item_id))
        replaced += 1
        receipt["models"].append(op)
    conn.commit()
    conn.close()
    receipt["finished_at"] = now()
    receipt["summary"] = {"processed": replaced, "archive_files": moved,
                          "hidden_extra_rows": hidden, "planned": len(operations)}
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gallery-db", type=Path, default=DEFAULT_GALLERY)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--generated-db", type=Path, default=DEFAULT_GENERATED)
    parser.add_argument("--generated-source-root", type=Path, default=DEFAULT_GENERATED_SOURCE)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--batch", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--only-with-old-files", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    ops = build_operations(args.gallery_db, args.generated_db, args.asset_root,
                           args.generated_source_root, args.only_with_old_files)
    receipt = apply_operations(args.gallery_db, args.asset_root, args.archive_root,
                               args.batch, ops, args.execute)
    out = args.archive_root / args.batch / "receipt.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"receipt": str(out), "executed": args.execute,
                      "summary": receipt["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
