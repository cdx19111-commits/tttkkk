"""
全局配置与模板定义
"""
import os
import json
from pathlib import Path
from typing import Dict, Any

# 项目根目录
BASE_DIR = Path(__file__).parent.resolve()
TEMPLATES_DIR = BASE_DIR / "templates"
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_PATH = BASE_DIR / "templates_config.json"

# 默认配置（首次运行时自动生成 templates_config.json）
DEFAULT_CONFIG = {
    "global": {
        "font_path": "",
        "prefer_ai_remove_bg": True,
        "bg_removal_tolerance": 30,
        "output_format": "png",
        "output_quality": 95
    },
    "templates": {
        "android_phone": {
            "name": "安卓手机",
            "template_path": str(TEMPLATES_DIR / "android_phone.png"),
            "product_image": {
                "center_x": 790,
                "center_y": 550,
                "max_width": 520,
                "max_height": 650,
                "fit_mode": "contain",
                "add_shadow": False
            },
            "product_name": {
                "center_x": 790,
                "center_y": 170,
                "font_size": 52,
                "color": "#1a1a1a",
                "max_width": 520,
                "align": "center",
                "auto_shrink": True
            }
        },
        "ios_phone": {
            "name": "苹果手机",
            "template_path": str(TEMPLATES_DIR / "ios_phone.png"),
            "product_image": {
                "center_x": 790,
                "center_y": 550,
                "max_width": 520,
                "max_height": 650,
                "fit_mode": "contain",
                "add_shadow": False
            },
            "product_name": {
                "center_x": 790,
                "center_y": 170,
                "font_size": 52,
                "color": "#1a1a1a",
                "max_width": 520,
                "align": "center",
                "auto_shrink": True
            }
        },
        "ipad": {
            "name": "iPad",
            "template_path": str(TEMPLATES_DIR / "ipad.png"),
            "product_image": {
                "center_x": 790,
                "center_y": 560,
                "max_width": 580,
                "max_height": 500,
                "fit_mode": "contain",
                "add_shadow": False
            },
            "product_name": {
                "center_x": 790,
                "center_y": 180,
                "font_size": 50,
                "color": "#1a1a1a",
                "max_width": 540,
                "align": "center",
                "auto_shrink": True
            }
        },
        "android_tablet": {
            "name": "安卓平板",
            "template_path": str(TEMPLATES_DIR / "android_tablet.png"),
            "product_image": {
                "center_x": 790,
                "center_y": 560,
                "max_width": 580,
                "max_height": 500,
                "fit_mode": "contain",
                "add_shadow": False
            },
            "product_name": {
                "center_x": 790,
                "center_y": 180,
                "font_size": 50,
                "color": "#1a1a1a",
                "max_width": 540,
                "align": "center",
                "auto_shrink": True
            }
        },
        "watch": {
            "name": "手表",
            "template_path": str(TEMPLATES_DIR / "watch.png"),
            "product_image": {
                "center_x": 790,
                "center_y": 580,
                "max_width": 400,
                "max_height": 400,
                "fit_mode": "contain",
                "add_shadow": False
            },
            "product_name": {
                "center_x": 790,
                "center_y": 200,
                "font_size": 48,
                "color": "#1a1a1a",
                "max_width": 480,
                "align": "center",
                "auto_shrink": True
            }
        },
        "other_digital": {
            "name": "其他数码",
            "template_path": str(TEMPLATES_DIR / "other_digital.png"),
            "product_image": {
                "center_x": 790,
                "center_y": 550,
                "max_width": 520,
                "max_height": 600,
                "fit_mode": "contain",
                "add_shadow": False
            },
            "product_name": {
                "center_x": 790,
                "center_y": 170,
                "font_size": 50,
                "color": "#1a1a1a",
                "max_width": 520,
                "align": "center",
                "auto_shrink": True
            }
        }
    }
}


def load_config() -> Dict[str, Any]:
    """加载配置文件，如果不存在则创建默认配置。"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG


def save_config(cfg: Dict[str, Any]) -> None:
    """保存配置到文件。"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
