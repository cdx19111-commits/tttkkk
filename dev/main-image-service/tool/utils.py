"""
工具函数模块：字体查找、图片处理、抠图、文字渲染
"""
import os
import sys
import json
import re
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
except ImportError:
    print("[错误] 未安装 Pillow，请先运行: pip install Pillow")
    sys.exit(1)

# 尝试导入 rembg（AI 抠图）
try:
    from rembg import remove as rembg_remove
    REMBG_AVAILABLE = True
except ImportError:
    REMBG_AVAILABLE = False
    rembg_remove = None


def find_font(font_name_hint: str = "SourceHanSans", fallback_size: int = 40) -> Tuple[ImageFont.FreeTypeFont, str]:
    """
    在系统中查找中文字体，优先思源黑体。
    返回: (font_obj, font_path)
    """
    # macOS / Linux / Windows 常见思源黑体路径
    candidates = [
        os.path.expanduser("~/Library/Fonts/NotoSansCJKsc-Regular.otf"),
        os.path.expanduser("~/Library/Fonts/SourceHanSansCN-Regular.otf"),
        # macOS
        "/Library/Fonts/SourceHanSansCN-Regular.otf",
        "/Library/Fonts/SourceHanSansCN-Bold.otf",
        "/System/Library/Fonts/SourceHanSansCN-Regular.otf",
        "/Library/Fonts/NotoSansCJKsc-Regular.otf",
        "/Library/Fonts/NotoSansCJKsc-Bold.otf",
        "/System/Library/Fonts/NotoSansCJKsc-Regular.otf",
        # Windows
        "C:/Windows/Fonts/SourceHanSansCN-Regular.otf",
        "C:/Windows/Fonts/NotoSansCJKsc-Regular.otf",
        "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑 fallback
        "C:/Windows/Fonts/simhei.ttf",  # 黑体 fallback
        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]

    # 如果环境变量指定了字体路径，优先使用
    env_font = os.environ.get("IMAGE_GENERATOR_FONT")
    if env_font and os.path.exists(env_font):
        candidates.insert(0, env_font)

    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, fallback_size)
                return font, path
            except Exception:
                continue

    # 最后 fallback 到 PIL 默认字体（不支持中文）
    print(f"[警告] 未找到中文字体（{font_name_hint}），将使用默认字体（中文可能显示为方框）")
    print("[提示] 可通过以下方式指定字体：")
    print("       1. 设置环境变量: export IMAGE_GENERATOR_FONT=/path/to/font.otf")
    print("       2. 修改 templates_config.json 中的 font_path 字段")
    return ImageFont.load_default(), ""


def load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    """加载指定路径的字体。"""
    if not font_path or not os.path.exists(font_path):
        font, _ = find_font(fallback_size=size)
        return font
    return ImageFont.truetype(font_path, size)


def remove_background_simple(img: Image.Image, bg_color: Tuple[int, int, int] = (255, 255, 255),
                              tolerance: int = 30) -> Image.Image:
    """
    简单背景去除：将接近 bg_color 的像素变为透明。
    适用于白底/纯色背景的商品图。
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    datas = img.getdata()
    new_data = []
    r_bg, g_bg, b_bg = bg_color

    for item in datas:
        r, g, b, a = item
        # 计算与背景色的欧氏距离
        dist = ((r - r_bg) ** 2 + (g - g_bg) ** 2 + (b - b_bg) ** 2) ** 0.5
        if dist < tolerance:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append((r, g, b, a))

    img.putdata(new_data)
    return img


def remove_background_green(img: Image.Image,
                              bg_color: Tuple[int, int, int] = (0, 177, 64),
                              tolerance: int = 50) -> Image.Image:
    """
    绿幕背景去除：去除接近标准 chroma key green (0,177,64) 的像素。
    适用于绿幕背景的商品图，速度快且不误伤白色产品。
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    datas = img.getdata()
    new_data = []
    r_bg, g_bg, b_bg = bg_color

    for item in datas:
        r, g, b, a = item
        # 计算与绿幕色的欧氏距离
        dist = ((r - r_bg) ** 2 + (g - g_bg) ** 2 + (b - b_bg) ** 2) ** 0.5
        if dist < tolerance:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append((r, g, b, a))

    img.putdata(new_data)
    return img


def remove_background_ai(img: Image.Image) -> Image.Image:
    """
    使用 rembg 进行 AI 抠图。需要提前安装: pip install rembg[cpu]
    """
    if not REMBG_AVAILABLE:
        raise RuntimeError("rembg 未安装，无法使用 AI 抠图。请运行: pip install rembg[cpu]")
    return rembg_remove(img)


def smart_remove_background(img: Image.Image, prefer_ai: bool = True,
                               bg_mode: str = "auto") -> Image.Image:
    """
    智能抠图：根据 bg_mode 选择抠图方式。
    bg_mode: 'auto' 自动判断, 'green' 绿幕去除, 'white' 白底去除, 'ai' AI抠图
    """
    # 如果图片已经有 alpha 通道且存在透明像素，认为已经是抠好的图
    if img.mode == "RGBA":
        alpha = img.split()[-1]
        if alpha.getextrema()[0] < 255:
            print("[信息] 图片已包含透明通道，跳过抠图")
            return img

    if bg_mode == "green":
        print("[信息] 使用绿幕去除背景...")
        return remove_background_green(img)
    elif bg_mode == "white":
        print("[信息] 使用颜色阈值去除白底背景...")
        return remove_background_simple(img)
    elif bg_mode == "ai" or (bg_mode == "auto" and prefer_ai and REMBG_AVAILABLE):
        print("[信息] 使用 rembg AI 抠图...")
        return remove_background_ai(img)
    else:
        # auto 模式：检测是否为绿幕
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        sample = img.resize((50, 50))
        pixels = list(sample.getdata())
        green_count = sum(1 for r, g, b, a in pixels if g > r + 40 and g > b + 40)
        if green_count > len(pixels) * 0.5:
            print("[信息] 检测到绿幕背景，使用绿幕去除...")
            return remove_background_green(img)
        else:
            print("[信息] 使用颜色阈值去除白底背景...")
            return remove_background_simple(img)


def fit_image_in_box(img: Image.Image, box_width: int, box_height: int,
                     mode: str = "contain") -> Image.Image:
    """
    将图片等比缩放以适应指定区域。
    mode: 'contain' 保持比例完整显示；'cover' 保持比例填满区域（可能裁剪）
    """
    img_w, img_h = img.size
    ratio_w = box_width / img_w
    ratio_h = box_height / img_h

    if mode == "contain":
        ratio = min(ratio_w, ratio_h)
    elif mode == "cover":
        ratio = max(ratio_w, ratio_h)
    else:
        ratio = min(ratio_w, ratio_h)

    new_w = int(img_w * ratio)
    new_h = int(img_h * ratio)
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def paste_centered(base_img: Image.Image, overlay: Image.Image,
                   center_x: int, center_y: int) -> Image.Image:
    """
    将 overlay 以 (center_x, center_y) 为中心点粘贴到 base_img 上。
    """
    w, h = overlay.size
    left = center_x - w // 2
    top = center_y - h // 2
    base_img.paste(overlay, (left, top), overlay if overlay.mode == "RGBA" else None)
    return base_img


def paste_topleft(base_img: Image.Image, overlay: Image.Image,
                  x: int, y: int) -> Image.Image:
    """
    将 overlay 以左上角 (x, y) 为起点粘贴到 base_img 上。
    """
    base_img.paste(overlay, (x, y), overlay if overlay.mode == "RGBA" else None)
    return base_img


def draw_text_with_max_width(draw: ImageDraw.Draw, text: str, position: Tuple[int, int],
                              font: ImageFont.FreeTypeFont, fill: str,
                              max_width: int, align: str = "left",
                              origin: str = "center",
                              bold: bool = False) -> Tuple[int, int]:
    """
    在指定位置绘制文字，如果文字宽度超过 max_width，自动缩小字号。
    origin: 'center' 表示 position 是文字中心点；'topleft' 表示 position 是文字左上角。
    bold: 是否加粗（使用描边模拟）。
    返回实际绘制的文字尺寸 (width, height)。
    """
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    original_size = font.size

    # 如果文字太宽，按比例缩小字号
    if text_w > max_width:
        scale = max_width / text_w
        new_size = int(original_size * scale)
        if new_size < 10:
            new_size = 10  # 最小字号限制
        try:
            font_path = font.path if hasattr(font, "path") else None
            if font_path:
                font = ImageFont.truetype(font_path, new_size)
            else:
                font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

    x, y = position
    if origin == "topleft":
        # position 是墨迹左上角：把 ink bbox 的左上角对齐到 (x, y)
        x = x - bbox[0]
        y = y - bbox[1]
    else:
        # position 是墨迹中心：按 ink bbox 居中，而不是按字体 ascent 顶部
        if align == "center":
            x = x - (bbox[0] + bbox[2]) // 2
        elif align == "right":
            x = x - bbox[2]
        else:
            x = x - bbox[0]
        y = y - (bbox[1] + bbox[3]) // 2

    if bold:
        # 使用描边模拟加粗效果
        stroke_width = 1
        for dx in range(-stroke_width, stroke_width + 1):
            for dy in range(-stroke_width, stroke_width + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), text, font=font, fill=fill)

    draw.text((x, y), text, font=font, fill=fill)
    return text_w, text_h


def ensure_rgba(img: Image.Image) -> Image.Image:
    """确保图片为 RGBA 模式。"""
    if img.mode != "RGBA":
        return img.convert("RGBA")
    return img


def create_shadow(img: Image.Image, offset: Tuple[int, int] = (5, 5),
                  shadow_color: Tuple[int, int, int, int] = (0, 0, 0, 80),
                  blur_radius: int = 10) -> Image.Image:
    """
    为图片创建投影效果（可选增强）。
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    alpha = img.split()[-1]
    shadow = Image.new("RGBA", img.size, shadow_color)
    shadow.putalpha(alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))

    # 创建足够大的画布容纳阴影偏移
    canvas = Image.new("RGBA", (img.width + abs(offset[0]) + blur_radius * 2,
                                 img.height + abs(offset[1]) + blur_radius * 2), (0, 0, 0, 0))
    shadow_x = blur_radius + max(0, offset[0])
    shadow_y = blur_radius + max(0, offset[1])
    canvas.paste(shadow, (shadow_x, shadow_y), shadow)

    img_x = blur_radius + max(0, -offset[0])
    img_y = blur_radius + max(0, -offset[1])
    canvas.paste(img, (img_x, img_y), img)
    return canvas


def load_name_mapping(mapping_path: str = "name_mapping.json") -> dict:
    """加载名称映射表。"""
    if os.path.exists(mapping_path):
        with open(mapping_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def detect_template_from_filename(filename: str, mapping: dict = None) -> str:
    """
    从文件名自动识别商品类型，返回模板标识。
    规则：文件名包含关键词 → 对应模板
    优先级：更长的关键词优先，避免短关键词误匹配
    """
    if mapping is None:
        mapping = load_name_mapping()

    keywords_map = mapping.get("keywords_to_template", {})
    filename_lower = filename.lower()

    # 特殊规则：watch 相关关键词优先级最高
    watch_keywords = ["watch", "手环", "手表", "band"]
    for kw in watch_keywords:
        if kw in filename_lower:
            return "watch"

    # 特殊规则：pad 相关关键词
    pad_keywords = ["ipad", "ipadmini", "ipadair", "ipadpro", "galaxytab", "xiaomipad"]
    for kw in pad_keywords:
        if kw in filename_lower:
            return "ipad"

    # 特殊规则：airpods / 耳机 / 音箱
    audio_keywords = ["airpods", "耳机", "headphone", "speaker", "音箱"]
    for kw in audio_keywords:
        if kw in filename_lower:
            return "other_digital"

    # 按优先级匹配（更长的关键词优先）
    for keyword in sorted(keywords_map.keys(), key=len, reverse=True):
        if keyword.lower() in filename_lower:
            return keywords_map[keyword]

    return ""


def extract_product_name(filename: str, mapping: dict = None) -> str:
    """
    从文件名提取商品型号名称（文件名即名称）。
    逻辑：去掉扩展名 → 分隔符转空格 → 清理常见后缀词
    
    示例:
        '华为Mate60Pro_白色.png'     → '华为Mate60Pro 白色'
        'iPhone15ProMax_128G_blue'   → 'iPhone15ProMax 128G blue'
        '小米14 Pro-白底图'           → '小米14 Pro'
    """
    # 去掉扩展名
    name = os.path.splitext(os.path.basename(filename))[0]
    
    # 分隔符转空格
    name = name.replace('_', ' ').replace('-', ' ')
    
    # 清理常见后缀词（白底图、商品图、主图、副本等）
    name = re.sub(r'\s*(白底图|白底|商品图|主图|photo|image|pic|img)\d*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*(copy|副本|final|最终|new|新版)\d*$', '', name, flags=re.IGNORECASE)
    
    # 清理多余空格
    name = re.sub(r'\s+', ' ', name).strip()
    
    return name


def _format_model_part(part: str) -> str:
    """
    格式化单个型号部分。
    如 '15promax' → '15 Pro Max'
    """
    part_lower = part.lower()

    # 纯数字
    if re.match(r'^\d+$', part):
        return part

    # 数字 + 型号后缀 (如 15pro, 14max)
    m = re.match(r'^(\d+)(pro|max|ultra|plus|mini|se)$', part_lower)
    if m:
        return f"{m.group(1)} {m.group(2).upper()}"

    # 数字 + 型号 + 附加 (如 128g, 256g)
    m = re.match(r'^(\d+)(g|gb|mah|hz|inch|in)$', part_lower)
    if m:
        return f"{m.group(1)}{m.group(2).upper()}"

    # 字母+数字型号 (如 GT4, GT3)
    m = re.match(r'^([a-zA-Z]+)(\d+)$', part)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"

    # 已经是格式化好的，直接返回
    if re.match(r'^[A-Z][a-z]+$', part) or re.match(r'^\d+[A-Z]+\d*$', part):
        return part

    return ""


def _merge_brand_model_parts(parts: list, brands: dict) -> list:
    """
    将分散的品牌和型号部分合并。
    如 ['xiaomi', '14pro', '128g'] → ['xiaomi14pro', '128g']
    如 ['huawei', 'mate60pro', 'white'] → ['huaweimate60pro', 'white']
    """
    merged = []
    i = 0
    while i < len(parts):
        part = parts[i]
        part_lower = part.lower()

        # 检查当前部分是否为品牌
        is_brand = False
        for brand_key in brands:
            if part_lower == brand_key:
                is_brand = True
                break

        if is_brand and i + 1 < len(parts):
            # 合并品牌和下一个部分（型号）
            next_part = parts[i + 1]
            merged.append(part_lower + next_part.lower())
            i += 2
        else:
            merged.append(part)
            i += 1

    return merged


def auto_crop_square(img: Image.Image, target_size: int = 0) -> Image.Image:
    """
    自动裁切为 1:1 正方形（居中裁切），然后缩放到目标尺寸。
    target_size: 目标边长（0 表示不缩放，只裁切）
    """
    w, h = img.size
    side = min(w, h)

    # 居中裁切
    left = (w - side) // 2
    top = (h - side) // 2
    right = left + side
    bottom = top + side

    img = img.crop((left, top, right, bottom))

    # 缩放到目标尺寸
    if target_size > 0 and side != target_size:
        img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)

    return img
