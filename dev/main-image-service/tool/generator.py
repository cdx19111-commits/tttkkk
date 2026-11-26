"""
核心合成引擎：模板 + 商品图 + 文字 = 最终主图
"""
import os
import re
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont

from config import OUTPUT_DIR, load_config
from utils import (
    load_font, smart_remove_background, fit_image_in_box, auto_crop_square,
    paste_centered, paste_topleft, draw_text_with_max_width, ensure_rgba, create_shadow
)


def load_template(template_path: str) -> Image.Image:
    """加载模板图。"""
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"模板图不存在: {template_path}")
    img = Image.open(template_path)
    return ensure_rgba(img)


def compose_image(template_key: str, product_image_path: str, product_name: str,
                  cfg: Optional[Dict[str, Any]] = None) -> Image.Image:
    """
    合成单张商品主图。

    Args:
        template_key: 模板标识（如 'android_phone'）
        product_image_path: 白底商品图路径
        product_name: 商品型号名称
        cfg: 配置字典，None 时自动加载

    Returns:
        合成后的 PIL Image 对象
    """
    if cfg is None:
        cfg = load_config()

    global_cfg = cfg.get("global", {})
    templates = cfg.get("templates", {})

    if template_key not in templates:
        raise ValueError(f"未知模板: {template_key}。可用模板: {list(templates.keys())}")

    tpl_cfg = templates[template_key]

    # 1. 加载模板
    template = load_template(tpl_cfg["template_path"])

    # 2. 加载并处理商品图
    if not os.path.exists(product_image_path):
        raise FileNotFoundError(f"商品图不存在: {product_image_path}")

    img_cfg = tpl_cfg["product_image"]

    product_img = Image.open(product_image_path)

    # 自动 1:1 裁切（如果启用）
    auto_crop = img_cfg.get("auto_crop_square", True)
    target_size = img_cfg.get("square_size", 0)
    if auto_crop:
        product_img = auto_crop_square(product_img, target_size)

    bg_mode = global_cfg.get("bg_mode", "auto")
    product_img = smart_remove_background(
        product_img,
        prefer_ai=global_cfg.get("prefer_ai_remove_bg", True),
        bg_mode=bg_mode
    )

    # 3. 缩放商品图到指定区域
    product_img = fit_image_in_box(
        product_img,
        img_cfg["max_width"],
        img_cfg["max_height"],
        mode=img_cfg.get("fit_mode", "contain")
    )

    # 可选：添加阴影
    if img_cfg.get("add_shadow", False):
        product_img = create_shadow(product_img)

    # 4. 将商品图粘贴到模板
    img_origin = img_cfg.get("origin", "center")
    if img_origin == "topleft":
        template = paste_topleft(
            template, product_img,
            img_cfg["center_x"], img_cfg["center_y"]
        )
    else:
        template = paste_centered(
            template, product_img,
            img_cfg["center_x"], img_cfg["center_y"]
        )

    # 5. 绘制商品名称文字
    name_cfg = tpl_cfg["product_name"]
    draw = ImageDraw.Draw(template)

    font_path = global_cfg.get("font_path", "")
    font = load_font(font_path, name_cfg["font_size"])

    name_origin = name_cfg.get("origin", "center")
    name_bold = name_cfg.get("bold", False)
    draw_text_with_max_width(
        draw,
        product_name,
        (name_cfg["center_x"], name_cfg["center_y"]),
        font,
        name_cfg["color"],
        name_cfg.get("max_width", 500),
        align=name_cfg.get("align", "center"),
        origin=name_origin,
        bold=name_bold
    )

    return template


def save_image(img: Image.Image, output_path: str, quality: int = 95) -> str:
    """保存图片，自动根据后缀选择格式。"""
    ext = os.path.splitext(output_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        # JPEG 不支持透明，转为 RGB
        rgb_img = Image.new("RGB", img.size, (255, 255, 255))
        rgb_img.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        rgb_img.save(output_path, quality=quality)
    else:
        img.save(output_path)
    return output_path


def sanitize_filename(name: str) -> str:
    """将字符串转为安全的文件名。"""
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name.strip()


def batch_generate(items: list, output_dir: str = str(OUTPUT_DIR),
                   cfg: Optional[Dict[str, Any]] = None) -> list:
    """
    批量生成主图。

    Args:
        items: 列表，每项为 dict:
            {
                "template": "android_phone",
                "product_image": "/path/to/img.png",
                "product_name": "华为 Mate 60 Pro",
                "output_name": "可选，自定义输出文件名"
            }
        output_dir: 输出目录
        cfg: 配置字典

    Returns:
        成功生成的文件路径列表
    """
    if cfg is None:
        cfg = load_config()

    global_cfg = cfg.get("global", {})
    os.makedirs(output_dir, exist_ok=True)

    results = []
    total = len(items)

    for idx, item in enumerate(items, 1):
        template_key = item.get("template", "android_phone")
        product_image = item["product_image"]
        product_name = item["product_name"]
        output_name = item.get("output_name")

        if not output_name:
            ext = global_cfg.get("output_format", "png")
            safe_name = sanitize_filename(product_name)
            output_name = f"{safe_name}.{ext}"

        output_path = os.path.join(output_dir, output_name)

        print(f"[{idx}/{total}] 正在生成: {product_name} -> {output_name}")

        try:
            img = compose_image(template_key, product_image, product_name, cfg)
            save_image(
                img, output_path,
                quality=global_cfg.get("output_quality", 95)
            )
            results.append(output_path)
            print(f"       成功: {output_path}")
        except Exception as e:
            print(f"       失败: {e}")

    print(f"\n批量生成完成: 成功 {len(results)}/{total}")
    return results
