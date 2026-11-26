"""Preview engine: white-background cutout + template composition.

Reuses the vendor generator's layout/text utilities, but replaces the global
white-threshold cutout with an edge-connected flood fill and largest-component
selection so internal white product areas are preserved.
"""
from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw

from sample_config import FONT_PATH, TEMPLATES_CONFIG, TOOL_ROOT

sys.path.insert(0, str(TOOL_ROOT))
from generator import load_template, save_image  # noqa: E402
from utils import (auto_crop_square, draw_text_with_max_width, ensure_rgba,
                   fit_image_in_box, load_font, paste_centered)  # noqa: E402


def load_config() -> dict:
    cfg = json.loads(TEMPLATES_CONFIG.read_text(encoding="utf-8"))
    templates_dir = TOOL_ROOT / "templates"
    for key, value in cfg["templates"].items():
        value["template_path"] = str(templates_dir / f"{key}.png")
    cfg["global"]["font_path"] = FONT_PATH
    cfg["global"]["bg_mode"] = "white"
    cfg["global"]["prefer_ai_remove_bg"] = False
    return cfg


def _near_white(pixel: tuple[int, int, int], tolerance: int) -> bool:
    r, g, b = pixel[:3]
    return r >= 255 - tolerance and g >= 255 - tolerance and b >= 255 - tolerance


def cutout_product(path: Path, tolerance: int = 14) -> tuple[Image.Image, dict]:
    """Remove white background connected to the image border; keep largest object."""
    source = Image.open(path).convert("RGB")
    width, height = source.size
    pixels = source.load()

    # Flood fill from the border through near-white pixels.
    background = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def push(x: int, y: int) -> None:
        idx = y * width + x
        if background[idx]:
            return
        if not _near_white(pixels[x, y], tolerance):
            return
        background[idx] = 1
        queue.append((x, y))

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)
    while queue:
        x, y = queue.popleft()
        if x > 0:
            push(x - 1, y)
        if x + 1 < width:
            push(x + 1, y)
        if y > 0:
            push(x, y - 1)
        if y + 1 < height:
            push(x, y + 1)

    foreground = bytearray(width * height)
    for idx in range(width * height):
        if not background[idx]:
            foreground[idx] = 1

    # Connected components of foreground; keep the largest one.
    seen = bytearray(width * height)
    best: list[tuple[int, int]] = []
    components = 0
    for start in range(width * height):
        if not foreground[start] or seen[start]:
            continue
        components += 1
        current: list[tuple[int, int]] = []
        stack = [start]
        seen[start] = 1
        while stack:
            idx = stack.pop()
            x, y = idx % width, idx // width
            current.append((x, y))
            if x > 0:
                n = idx - 1
                if foreground[n] and not seen[n]:
                    seen[n] = 1
                    stack.append(n)
            if x + 1 < width:
                n = idx + 1
                if foreground[n] and not seen[n]:
                    seen[n] = 1
                    stack.append(n)
            if y > 0:
                n = idx - width
                if foreground[n] and not seen[n]:
                    seen[n] = 1
                    stack.append(n)
            if y + 1 < height:
                n = idx + width
                if foreground[n] and not seen[n]:
                    seen[n] = 1
                    stack.append(n)
        if len(current) > len(best):
            best = current

    if not best:
        raise ValueError("cutout produced an empty foreground")

    keep = bytearray(width * height)
    xs, ys = [], []
    for x, y in best:
        keep[y * width + x] = 1
        xs.append(x)
        ys.append(y)
    left, right = max(0, min(xs)), min(width - 1, max(xs))
    top, bottom = max(0, min(ys)), min(height - 1, max(ys))

    rgba = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    src = source.load()
    out = rgba.load()
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if keep[y * width + x]:
                r, g, b = src[x, y]
                out[x, y] = (r, g, b, 255)

    # Crop to the object with a small breathing margin.
    margin = max(2, int(min(width, height) * 0.02))
    box = (max(0, left - margin), max(0, top - margin),
           min(width, right + 1 + margin), min(height, bottom + 1 + margin))
    cropped = rgba.crop(box)
    stats = {
        "components": components,
        "foreground_pixels": len(best),
        "image_pixels": width * height,
        "largest_ratio": len(best) / float(width * height),
        "bbox": box,
        "cropped_size": cropped.size,
    }
    return cropped, stats


def foreground_luminance(image: Image.Image) -> float:
    pixels = [p for p in image.getdata() if p[3] > 0]
    if not pixels:
        return 255.0
    return sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b, _ in pixels) / len(pixels)


def compose(template_key: str, product: Image.Image, text: str,
            cfg: dict | None = None) -> Image.Image:
    cfg = cfg or load_config()
    template_cfg = cfg["templates"][template_key]
    template = load_template(template_cfg["template_path"])

    if template_cfg["product_image"].get("auto_crop_square", False):
        product = auto_crop_square(product, template_cfg["product_image"].get("square_size", 0))
    product = fit_image_in_box(
        product,
        template_cfg["product_image"]["max_width"],
        template_cfg["product_image"]["max_height"],
        mode=template_cfg["product_image"].get("fit_mode", "contain"),
    )
    template = paste_centered(
        template, product,
        template_cfg["product_image"]["center_x"],
        template_cfg["product_image"]["center_y"],
    )

    name_cfg = template_cfg["product_name"]
    draw = ImageDraw.Draw(template)
    font = load_font(cfg["global"].get("font_path", ""), name_cfg["font_size"])
    draw_text_with_max_width(
        draw, text, (name_cfg["center_x"], name_cfg["center_y"]),
        font, name_cfg["color"], name_cfg.get("max_width", 500),
        align=name_cfg.get("align", "center"),
        origin=name_cfg.get("origin", "center"),
        bold=name_cfg.get("bold", False),
    )
    return template
