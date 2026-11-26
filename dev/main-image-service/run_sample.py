#!/usr/bin/env python3
"""Generate a 10-model local sample for the main-image workflow.

The sample uses:
  * shop 129675602 from the real read-only gallery snapshot;
  * real A-level white preview images as source;
  * real existing main images in a sandbox (download them first);
  * the vendor's six generic 1080x1080 templates;
  * edge-connected cutout + largest-object selection.

It never touches the production gallery or the source snapshot.  All writes go
under dev/main-image-service/sample/.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from catalog import Target, is_black, load_targets
from sample_config import (BACKUP_ROOT, FONT_PATH, META_DB, REAL_V03, REPORT,
                    SAMPLE_ROOT, SHOP_ID, SHOP_NAME, TARGET_MODELS)
from engine import compose, cutout_product, foreground_luminance, load_config, save_image
from store import (backup_existing, connect, existing_path, get_generated,
                   get_source, now, sha256, upsert_generated, upsert_source,
                   write_output_atomic)


def select_source(conn, target: Target, cfg: dict) -> tuple[object, Image.Image, dict, str]:
    """Select a source image, persist it, and return cutout details."""
    saved = get_source(conn, SHOP_ID, target.key)
    ordered = list(target.sources)
    if saved:
        ordered.sort(key=lambda item: item.asset_id != saved["asset_id"])
    candidates = []
    for source in ordered:
        path = REAL_V03 / source.local_path
        if not path.is_file():
            continue
        try:
            cutout, stats = cutout_product(path)
        except Exception as exc:
            continue
        if stats["largest_ratio"] < 0.02:
            continue
        luminance = foreground_luminance(cutout)
        candidates.append((source, cutout, stats, luminance))
    if not candidates:
        raise RuntimeError("没有可用的 A 级源图")

    if saved:
        for item in candidates:
            if item[0].asset_id == saved["asset_id"]:
                source, cutout, stats, _luminance = item
                return source, cutout, stats, "沿用已选源图"
    black = [item for item in candidates if is_black(item[0].color)]
    pool = black or candidates
    # Black first, then the darkest product, then stable id.
    selected = sorted(pool, key=lambda item: (item[3], item[0].asset_id))[0]
    reason = "黑色优先" if black else "深色优先"
    upsert_source(conn, SHOP_ID, target.key, selected[0].asset_id, selected[0].color,
                  selected[0].local_path, sha256(REAL_V03 / selected[0].local_path), reason)
    source, cutout, stats, _luminance = selected
    return source, cutout, stats, reason


def output_relative(target: Target) -> str:
    return target.existing.local_path


def make_contact_sheet(rows: list[dict], output: Path) -> None:
    if not rows:
        return
    thumbs = []
    for row in rows:
        path = SAMPLE_ROOT / "v03" / row["output_path"]
        if not path.is_file():
            continue
        image = Image.open(path).convert("RGB")
        image.thumbnail((320, 320))
        thumbs.append((row, image))
    columns = 2
    cell_w, cell_h = 360, 390
    rows_count = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows_count * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(FONT_PATH, 22)
    for index, (row, image) in enumerate(thumbs):
        x = (index % columns) * cell_w + 20
        y = (index // columns) * cell_h + 10
        sheet.paste(image, (x, y))
        draw.text((x, y + 330), f"{row['model_name']} · {row.get('color', '')}", fill="black", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="regenerate even if current")
    args = parser.parse_args()

    sample_v03 = SAMPLE_ROOT / "v03"
    sample_v03.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    conn = connect()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    targets = load_targets(SHOP_ID, TARGET_MODELS, REAL_V03 / "gallery-preview.db")
    print(f"目标店铺：{SHOP_NAME} ({SHOP_ID})，型号 {len(targets)} 个")

    rows = []
    counts = {"generated": 0, "skipped_current": 0, "failed": 0}
    for target in targets:
        previous = get_generated(conn, SHOP_ID, target.key)
        if previous and previous["status"] == "ok" and not args.force:
            output = sample_v03 / previous["output_path"]
            if output.is_file():
                counts["skipped_current"] += 1
                rows.append(dict(previous))
                print(f"[跳过] {target.model_name}: 已是当前版本")
                continue

        try:
            source, cutout, stats, reason = select_source(conn, target, cfg)
            relative = output_relative(target)
            old_file = existing_path(relative)
            backup = backup_existing(relative, run_id) if old_file.is_file() else None
            image = compose(target.template_key, cutout, target.model_name, cfg)
            output = sample_v03 / relative
            write_output_atomic(output, image, cfg["global"].get("output_quality", 95))
            output_sha = sha256(output)
            upsert_generated(
                conn, shop_id=SHOP_ID, model_key=target.key, model_name=target.model_name,
                asset_id=source.asset_id, source_path=str(REAL_V03 / source.local_path),
                source_sha=sha256(REAL_V03 / source.local_path),
                template_key=target.template_key, template_version=1,
                output_path=relative, output_sha=output_sha,
                old_path=str(backup[0]) if backup else "",
                backup_path=str(backup[1]) if backup else "",
                status="ok", error="", generated_at=now(), updated_at=now(),
            )
            counts["generated"] += 1
            rows.append({
                "model_name": target.model_name, "model_key": target.key,
                "color": source.color, "asset_id": source.asset_id,
                "template": target.template_key, "output_path": relative,
                "old_path": str(backup[0]) if backup else "",
                "backup_path": str(backup[1]) if backup else "",
                "selection_reason": reason,
                "components": stats["components"], "largest_ratio": round(stats["largest_ratio"], 4),
                "status": "ok",
            })
            print(f"[生成] {target.model_name} <- {source.color} ({reason}) -> {relative}")
        except Exception as exc:
            counts["failed"] += 1
            upsert_generated(
                conn, shop_id=SHOP_ID, model_key=target.key, model_name=target.model_name,
                asset_id="", source_path="", source_sha="", template_key=target.template_key,
                template_version=1, output_path="", output_sha="", old_path="",
                backup_path="", status="failed", error=f"{type(exc).__name__}: {exc}",
                generated_at="", updated_at=now(),
            )
            rows.append({"model_name": target.model_name, "model_key": target.key,
                         "status": "failed", "error": str(exc)})
            print(f"[失败] {target.model_name}: {exc}")

    report = {
        "shop": {"id": SHOP_ID, "name": SHOP_NAME},
        "run_id": run_id, "generated_at": now(),
        "counts": counts, "items": rows,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    sheet = SAMPLE_ROOT / "contact_sheet.png"
    make_contact_sheet(rows, sheet)
    print(json.dumps({"counts": counts, "report": str(REPORT), "contact_sheet": str(sheet)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
