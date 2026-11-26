#!/usr/bin/env python3
"""Dry-run archive/replacement plan for generated Douyin main images.

Reads a gallery DB and the main-image service DB, but NEVER writes them.
It produces a per-file plan showing which old files would be archived and
which DB rows would be hidden, plus a per-model summary.

Usage:
  python archive_plan.py \
    --gallery-db ../local/data/real/v03/gallery.db \
    --asset-root ../local/data/real/v03 \
    --generated-db service_data/main_images.db \
    --out archive_plans/20261126
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from catalog import _parse_item, brand_group, model_key, target_key

ROOT = Path(__file__).resolve().parent
DEFAULT_GALLERY = ROOT.parent / "local" / "data" / "real" / "v03" / "gallery.db"
DEFAULT_ASSET_ROOT = ROOT.parent / "local" / "data" / "real" / "v03"
DEFAULT_GENERATED = ROOT / "service_data" / "main_images.db"
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def item_key(source_path: str) -> tuple[str, str, str, str] | None:
    parts = str(source_path).split("/")
    if len(parts) < 3 or parts[0] != "抖音主图":
        return None
    shop = parts[1]
    parsed = _parse_item(source_path)
    if not parsed:
        return None
    category, brand, model = parsed
    return shop, category, target_key(brand_group(brand), model_key(model)), model


def load_gallery(db_path: Path) -> dict[tuple[str, str], list[dict]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    try:
        rows = conn.execute("""select item_id, source_path, name, extension, local_path,
                                      width, height, sha256, size_bytes
                               from douyin_gallery_items""").fetchall()
    finally:
        conn.close()
    for row in rows:
        parsed = item_key(row["source_path"])
        if not parsed:
            continue
        shop, category, key, model = parsed
        grouped[(shop, key)].append({**dict(row), "category": category,
                                     "parsed_model": model})
    return grouped


def load_generated(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""select shop_id, model_key, model_name, output_path,
                                      output_sha, status, generated_at
                               from generated_mains where status='ok'""").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def choose_canonical(candidates: list[dict], generated: dict) -> dict | None:
    if not candidates:
        return None
    output_path = str(generated.get("output_path") or "")
    for row in candidates:
        if row["local_path"] == output_path:
            return row
    wanted = str(generated.get("model_name") or "").casefold()
    for row in candidates:
        if Path(row["name"]).stem.casefold() == wanted:
            return row
    return sorted(candidates, key=lambda x: (x["local_path"], x["item_id"]))[0]


def build_plan(gallery_db: Path, asset_root: Path, generated_db: Path) -> dict:
    gallery = load_gallery(gallery_db)
    generated = load_generated(generated_db)
    model_rows, file_rows = [], []
    summary = {
        "generated_ok": len(generated),
        "with_existing_rows": 0,
        "no_existing_rows": 0,
        "multi_color_models": 0,
        "models_with_old_files": 0,
        "files_to_archive": 0,
        "files_already_generated": 0,
        "files_missing": 0,
        "generated_file_missing": 0,
    }
    for item in generated:
        shop = item["shop_id"]
        key = item["model_key"]
        candidates = gallery.get((shop, key), [])
        output_path = str(item.get("output_path") or "")
        output_file = asset_root / output_path if output_path else None
        output_exists = bool(output_file and output_file.is_file())
        if output_exists:
            output_sha = sha256(output_file)
        else:
            output_sha = str(item.get("output_sha") or "")
            summary["generated_file_missing"] += 1
        canonical = choose_canonical(candidates, item)
        if candidates:
            summary["with_existing_rows"] += 1
        else:
            summary["no_existing_rows"] += 1
        if len(candidates) > 1:
            summary["multi_color_models"] += 1
        old_files = []
        missing_files = []
        already = []
        for row in candidates:
            path = asset_root / str(row["local_path"] or "")
            if not path.is_file():
                missing_files.append(row["local_path"])
                file_rows.append({
                    "shop_id": shop, "model_key": key, "item_id": row["item_id"],
                    "local_path": row["local_path"], "action": "missing",
                    "reason": "file_not_on_disk", "canonical": bool(canonical and row["item_id"] == canonical["item_id"]),
                })
                continue
            digest = sha256(path)
            is_generated = bool(output_sha and digest == output_sha)
            if is_generated:
                already.append(row["local_path"])
                action = "keep_generated"
                reason = "file_sha_matches_generated"
            else:
                old_files.append(row["local_path"])
                action = "archive"
                reason = "old_main_image_before_generated_replacement"
            file_rows.append({
                "shop_id": shop, "model_key": key, "item_id": row["item_id"],
                "local_path": row["local_path"], "sha256": digest,
                "action": action, "reason": reason,
                "canonical": bool(canonical and row["item_id"] == canonical["item_id"]),
            })
        if old_files:
            summary["models_with_old_files"] += 1
            summary["files_to_archive"] += len(old_files)
        summary["files_already_generated"] += len(already)
        summary["files_missing"] += len(missing_files)
        model_rows.append({
            "shop_id": shop, "shop_name": SHOP_NAMES.get(shop, shop),
            "model_key": key, "model_name": item["model_name"],
            "generated_status": item["status"],
            "generated_exists": output_exists,
            "generated_path": output_path,
            "candidate_rows": len(candidates),
            "canonical_item_id": canonical["item_id"] if canonical else "",
            "canonical_path": canonical["local_path"] if canonical else "",
            "old_files_to_archive": old_files,
            "already_generated_files": already,
            "missing_files": missing_files,
            "planned_visible_rows": 1 if output_exists else 0,
            "action": "archive_and_keep_one" if (old_files or len(candidates) > 1) else "noop",
        })
    return {"generated_at": now(), "summary": summary,
            "models": model_rows, "files": file_rows}


def write_plan(plan: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "models.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "shop_id", "shop_name", "model_key", "model_name", "generated_status",
            "generated_exists", "generated_path", "candidate_rows", "canonical_item_id",
            "canonical_path", "old_files_to_archive", "already_generated_files",
            "missing_files", "planned_visible_rows", "action"])
        writer.writeheader()
        for row in plan["models"]:
            out = dict(row)
            for key in ("old_files_to_archive", "already_generated_files", "missing_files"):
                out[key] = json.dumps(out[key], ensure_ascii=False)
            writer.writerow(out)
    with (out_dir / "files.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "shop_id", "model_key", "item_id", "local_path", "sha256",
            "action", "reason", "canonical"])
        writer.writeheader()
        writer.writerows(plan["files"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gallery-db", type=Path, default=DEFAULT_GALLERY)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--generated-db", type=Path, default=DEFAULT_GENERATED)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "archive_plans" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    plan = build_plan(args.gallery_db, args.asset_root, args.generated_db)
    write_plan(plan, args.out)
    print(json.dumps({"out": str(args.out), "summary": plan["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
