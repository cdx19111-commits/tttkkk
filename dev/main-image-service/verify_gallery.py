#!/usr/bin/env python3
"""Verify generated mains are stored at gallery paths and visible to the gallery API.

Read-only. Checks every successful generated row for:
- output file exists and matches its recorded SHA
- a gallery row exists at the same local_path and is active
- width/height are 1080 and size matches
- no second active row for the same shop/model

Usage:
  python verify_gallery.py --gallery-db ../local/data/real/v03/gallery-preview.db \
    --asset-root ../local/data/real/v03 --generated-db service_data/main_images.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from archive_plan import item_key, sha256


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gallery-db", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--generated-db", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    gen = sqlite3.connect(f"file:{args.generated_db}?mode=ro", uri=True)
    gen.row_factory = sqlite3.Row
    generated = gen.execute("""select shop_id, model_key, model_name, output_path,
        output_sha, status from generated_mains where status='ok'""").fetchall()
    gen.close()

    gallery = sqlite3.connect(f"file:{args.gallery_db}?mode=ro", uri=True)
    gallery.row_factory = sqlite3.Row
    has_active = any(row[1] == "is_active"
                     for row in gallery.execute("pragma table_info(douyin_gallery_items)"))
    expr = "coalesce(is_active,1)" if has_active else "1"
    rows = gallery.execute(f"""select item_id, source_path, local_path, size_bytes,
        width, height, sha256, {expr} is_active from douyin_gallery_items""").fetchall()
    gallery.close()

    by_model: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    by_path: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        parsed = item_key(row["source_path"])
        if parsed:
            by_model[(parsed[0], parsed[2])].append(row)
        by_path[str(row["local_path"])].append(row)

    results = []
    summary = defaultdict(int)
    for item in generated:
        target = args.asset_root / str(item["output_path"] or "")
        issues = []
        if not target.is_file():
            issues.append("output_missing")
        else:
            if item["output_sha"] and sha256(target) != item["output_sha"]:
                issues.append("output_sha_mismatch")
        candidates = by_model.get((item["shop_id"], item["model_key"]), [])
        active = [r for r in candidates if r["is_active"]]
        canonical = [r for r in active if r["local_path"] == item["output_path"]]
        if not candidates:
            issues.append("gallery_row_missing")
        if not canonical:
            issues.append("canonical_not_active")
        if len(active) > 1:
            issues.append("duplicate_active_rows")
        if canonical:
            row = canonical[0]
            if (row["width"], row["height"]) != (1080, 1080):
                issues.append("gallery_size_not_1080")
            if target.is_file() and row["size_bytes"] != target.stat().st_size:
                issues.append("gallery_size_mismatch")
            if item["output_sha"] and row["sha256"] and row["sha256"] != item["output_sha"]:
                issues.append("gallery_sha_mismatch")
        status = "ok" if not issues else "failed"
        summary[status] += 1
        for issue in issues:
            summary[issue] += 1
        results.append({"shop_id": item["shop_id"], "model_key": item["model_key"],
                        "model_name": item["model_name"], "status": status,
                        "issues": issues, "output_path": item["output_path"]})

    report = {"summary": dict(summary), "items": results}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    bad = [item for item in results if item["status"] != "ok"]
    if bad:
        print(json.dumps(bad[:10], ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
