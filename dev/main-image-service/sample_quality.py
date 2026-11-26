#!/usr/bin/env python3
"""Generate a review sheet from production-resolution source images.

Reads only a local manifest and already-downloaded source files. It does not
touch the gallery database or production storage.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog import brand_group, orientation_ok  # noqa: E402
from engine import compose, cutout_product, load_config  # noqa: E402


def safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value).strip("._")
    return cleaned or fallback


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--filter-orientation", action="store_true")
    args = parser.parse_args()

    rows = json.loads(args.manifest.read_text(encoding="utf-8"))
    cfg = load_config()
    args.out.mkdir(parents=True, exist_ok=True)
    report = []
    for index, row in enumerate(rows, 1):
        source = args.source / row["local_path"]
        if not source.is_file():
            report.append({**row, "status": "missing_source"})
            continue
        try:
            cutout, stats = cutout_product(source)
            if args.filter_orientation and not orientation_ok("phone", cutout.size):
                report.append({**row, "status": "rejected_orientation",
                               "cutout_size": cutout.size})
                continue
            template_key = "ios_phone" if brand_group(row["brand_name"]) == "apple" else "android_phone"
            image = compose(template_key, cutout, row["model_name"], cfg)
            name = f"{index:02d}_{safe_name(row['model_name'], 'model')}_{safe_name(row['color'], 'color')}.png"
            target = args.out / name
            image.save(target, "PNG")
            report.append({**row, "status": "ok", "output": str(target),
                           "largest_ratio": stats.get("largest_ratio"),
                           "cutout_size": stats.get("cropped_size")})
        except Exception as exc:
            report.append({**row, "status": f"failed: {type(exc).__name__}: {exc}"})
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    ok = sum(item["status"] == "ok" for item in report)
    rejected = sum(item["status"] == "rejected_orientation" for item in report)
    print(json.dumps({"out": str(args.out), "ok": ok, "rejected": rejected,
                      "total": len(report)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
