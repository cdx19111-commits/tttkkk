"""Local prototype configuration for the main-image generator sample."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[1]


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser() if value else default


REAL_V03 = _env_path("MAIN_IMAGE_V03_ROOT", WORKSPACE / "dev/local/data/real/v03")
REAL_DB = _env_path("MAIN_IMAGE_GALLERY_DB", REAL_V03 / "gallery-preview.db")
OUTPUT_V03 = _env_path("MAIN_IMAGE_OUTPUT_ROOT", REAL_V03)
GAIA_POOL = _env_path("MAIN_IMAGE_GAIA_POOL", WORKSPACE / "dev/local/data/real/gaia_model_pool.json")

SAMPLE_ROOT = ROOT / "sample"
SAMPLE_V03 = SAMPLE_ROOT / "v03"
BACKUP_ROOT = SAMPLE_ROOT / "backup"
META_DB = SAMPLE_ROOT / "main_images.db"
REPORT = SAMPLE_ROOT / "sample_report.json"

TOOL_ROOT = ROOT / "tool"
TEMPLATES_CONFIG = _env_path("MAIN_IMAGE_TEMPLATES_CONFIG", TOOL_ROOT / "templates_config.json")
FONT_PATH = os.getenv("MAIN_IMAGE_FONT_PATH", "/System/Library/Fonts/Hiragino Sans GB.ttc")

SHOP_ID = "129675602"
SHOP_NAME = "爱回收官方旗舰店"
TARGET_MODELS = [
    "iPhone 11",
    "iPhone 13",
    "iPhone 14 Pro Max",
    "iPhone 15 Pro Max",
    "iPhone 16 Pro",
    "小米 14",
    "OPPO Reno13",
    "vivo X100",
    "三星 Galaxy S24",
    "realme GT6",
]
