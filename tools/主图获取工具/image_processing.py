from collections import deque
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps


SIZE = 800
# Good case（正背同框）主体高度约占画布 56%，上下留白约各 22%。
SUBJECT_HEIGHT_RATIO = 0.56
WATERMARK_TEMPLATE = Path(__file__).resolve().parent / "assets" / "zol-watermark.png"
FRONT_AND_BACK = "疑似正背同框"
POSSIBLE_PAIR = "疑似同框待确认"
SINGLE_VIEW = "疑似单面"
UNCONFIRMED_VIEW = "构图待确认"
# 只保留双面（含待确认同框）；单面/构图不明直接丢弃。
KEEP_VIEWS = {FRONT_AND_BACK, POSSIBLE_PAIR}


def components(mask):
    height, width = mask.shape
    pending = set(map(tuple, np.argwhere(mask).tolist()))
    groups = []
    while pending:
        seed = pending.pop()
        queue = deque([seed])
        ys, xs = [], []
        while queue:
            y, x = queue.popleft()
            ys.append(y)
            xs.append(x)
            for cell in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                if cell in pending:
                    pending.remove(cell)
                    queue.append(cell)
        if len(xs) >= max(60, width * height * .008):
            groups.append((min(xs), min(ys), max(xs)+1, max(ys)+1))
    return groups


@lru_cache(maxsize=1)
def watermark_template():
    with Image.open(WATERMARK_TEMPLATE) as image:
        return image.convert("L")


def window_sums(array, height, width):
    integral = np.pad(array.cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0)))
    return integral[height:, width:] - integral[:-height, width:] - integral[height:, :-width] + integral[:-height, :-width]


def correlation_scores(pixels, signal):
    height, width = signal.shape
    rows, columns = pixels.shape
    shape = (rows + height - 1, columns + width - 1)
    correlation = np.fft.irfft2(
        np.fft.rfft2(pixels, s=shape, axes=(0, 1)) *
        np.fft.rfft2(signal[::-1, ::-1], s=shape, axes=(0, 1)), s=shape, axes=(0, 1))
    correlation = correlation[height-1:rows, width-1:columns]
    count = width * height
    sums = window_sums(pixels, height, width)
    energy = window_sums(pixels**2, height, width) - sums**2 / count
    scores = correlation / np.sqrt(np.maximum(energy, 1) * np.sum(signal**2))
    return scores, sums / count, energy / count


def local_detail(image):
    smooth = image.filter(ImageFilter.GaussianBlur(2))
    detail = np.asarray(smooth, dtype=np.float64) - np.asarray(image, dtype=np.float64)
    return np.clip(detail, -8, 8)


def scan_watermark(photo):
    """Return the best logo match and whether a weaker match needs review."""
    left, top = int(photo.width * .67), int(photo.height * .70)
    region = photo.crop((left, top, photo.width, photo.height)).convert("L")
    scale = min(.5, 400 / max(region.size))
    region = region.resize(tuple(max(1, round(size * scale)) for size in region.size), Image.Resampling.BILINEAR)
    darkness = 255 - np.asarray(region, dtype=np.float64)
    detail = local_detail(region)
    template = watermark_template()
    best = (.68, None)
    uncertain = False
    for factor in (.85, 1.0, 1.18):
        width, height = (round(size * scale * factor) for size in template.size)
        if width < 12 or height < 8 or width > region.width or height > region.height:
            continue
        logo = template.resize((width, height), Image.Resampling.BILINEAR)
        signal = 255 - np.asarray(logo, dtype=np.float64)
        signal -= signal.mean()
        scores, mean, variance = correlation_scores(darkness, signal)
        light_scores = np.where((mean < 11) & (variance > 2), scores, -1)
        # Remove broad shading so a mark over a screen or case can also match.
        signal = local_detail(logo)
        signal -= signal.mean()
        scores, _, variance = correlation_scores(detail, signal)
        detail_scores = np.where(variance > 1, np.abs(scores), -1)
        scores = np.maximum(light_scores, detail_scores)
        uncertain |= bool(np.max(light_scores) >= .53 or np.max(detail_scores) >= .42)
        y, x = map(int, np.unravel_index(np.argmax(scores), scores.shape))
        if scores[y, x] > best[0]:
            scale_x = region.width / (photo.width-left)
            scale_y = region.height / (photo.height-top)
            best = (scores[y, x], (left + x / scale_x, top + y / scale_y,
                                   left + (x+width) / scale_x, top + (y+height) / scale_y))
    return best[1], uncertain


def watermark_may_overlap(photo, crop_box):
    x0, y0, x1, y1 = crop_box
    if x1 <= photo.width * .67 or y1 <= photo.height * .70:
        return False
    mark, uncertain = scan_watermark(photo)
    if mark is not None:
        left, top, right, bottom = mark
        return x0 < right and x1 > left and y0 < bottom and y1 > top
    if uncertain:
        return True
    if x1 <= photo.width * .75 or y1 <= photo.height * .75:
        return False
    # An uninterrupted blank strip wide enough for the logo provides evidence
    # of an unmarked render. An obscured or textured corner still needs review.
    margin = photo.width - x1
    if margin < max(watermark_template().width * 1.5, photo.width * .10):
        return True
    strip = np.asarray(photo.crop((x1, int(photo.height * .72), photo.width, photo.height)))
    return float(np.mean(strip.min(axis=2) >= 248)) < .995


def has_white_frame(photo):
    # Check the original pixels when a full-height render leaves only a slim margin.
    pixels = np.asarray(photo)
    edges = (pixels[:2], pixels[-2:], pixels[:, :2], pixels[:, -2:])
    return all(float(np.mean(edge.min(axis=2) >= 244)) >= .98 for edge in edges)


def split_phone_views(pixels):
    height, width = pixels.shape[:2]
    middle = pixels[int(height * .19):int(height * .84)]
    gradients = np.max(np.abs(np.diff(middle, axis=1)), axis=2)
    # Allow a narrow band so the same edge can move slightly along the phone.
    vertical = np.lib.stride_tricks.sliding_window_view(gradients, 3, axis=1).max(axis=2)
    strength = np.quantile(vertical, .30, axis=0)
    start, end = int(width * .26), int(width * .73)
    if min(np.max(strength[:start]), np.max(strength[end:])) < 7:
        return None

    # Separate phones may have a white gap. Overlapping phones share a long
    # vertical seam between the front glass and the rear case.
    gap_columns = np.mean(middle.min(axis=2) >= 248, axis=0) > .92
    gap = np.flatnonzero(gap_columns[start:end] & gap_columns[start+1:end+1])
    if gap.size:
        split = start + int(gap[np.argmin(abs(gap + start - width / 2))]) + 1
    else:
        seam = start + int(np.argmax(strength[start:end]))
        if strength[seam] < 11:
            return None
        split = seam + 2
    return pixels[:, :split], pixels[:, split:]


def panel_signals(panel):
    height, width = panel.shape[:2]
    gray = panel.mean(axis=2)
    top = gray[int(height * .04):int(height * .27), int(width * .08):int(width * .88)]
    center = gray[int(height * .30):int(height * .78), int(width * .11):int(width * .89)]

    def detail(area):
        return np.maximum(np.abs(area[1:, 1:] - area[:-1, 1:]),
                          np.abs(area[1:, 1:] - area[1:, :-1]))

    top80, top95 = np.quantile(detail(top), (.80, .95))
    center80, center95 = np.quantile(detail(center), (.80, .95))
    # Rear lenses form a compact textured region above a smoother case.
    camera = top80 >= 8 and top95 >= 30 and top95 >= 1.35 * center95 + 12

    middle = gray[int(height * .22):int(height * .80)]
    inner_start = round(width * .12)
    inner_end = max(inner_start + 1, round(width * .20))
    rim = []
    for edge in (middle, middle[:, ::-1]):
        rail = edge[:, :max(3, round(width * .11))].min(axis=1)
        inner = np.median(edge[:, inner_start:inner_end], axis=1)
        rim.append(float(np.quantile(inner - rail, .20)))
    return camera, min(rim), center80, center95


def has_front_and_back(panels):
    signals = [panel_signals(panel) for panel in panels]
    for front_index, front_panel in enumerate(panels):
        back, front = signals[1-front_index], signals[front_index]
        camera, back_rim, back_texture, back_detail = back
        front_camera, front_rim, front_texture, front_detail = front
        height, width = front_panel.shape[:2]
        if not camera or width < .26 * height:
            continue
        screen_frame = front_rim >= 8 and (not front_camera or front_rim >= back_rim + 20)
        screen_detail = front_texture >= back_texture + 2 and front_detail >= back_detail + 10
        if screen_frame or screen_detail:
            return True
    return False


def has_phone_outline(pixels):
    height, width = pixels.shape[:2]
    rows = np.arange(round(height * .11), round(height * .91))
    foreground = pixels[rows].min(axis=2) < 242
    filled = foreground.any(axis=1)
    if not filled.any():
        return False
    left = foreground.argmax(axis=1)
    right = width-1-foreground[:, ::-1].argmax(axis=1)
    tolerance = np.ones(7 if width >= 160 else 5, dtype=int)
    # A tilted handset still has a long, nearly straight outside edge.
    for edge in (left[filled], right[filled]):
        for slope in np.linspace(-.65, .65, 27):
            offsets = np.rint(edge - slope * rows[filled]).astype(int)
            histogram = np.bincount(offsets - offsets.min())
            if np.convolve(histogram, tolerance).max() >= .65 * len(rows):
                return True
    return False


def prepare(source: Path, destination: Path, *, require_product=False):
    """Return candidate metadata, or None when the white background is uncertain."""
    with Image.open(source) as file:
        if file.width * file.height > 28_000_000:
            return None
        original = ImageOps.exif_transpose(file)
        photo = Image.new("RGB", original.size, "white")
        photo.paste(original, mask=original.getchannel("A") if "A" in original.getbands() else None)
    if min(photo.size) < 320:
        return None

    sample = photo.copy()
    sample.thumbnail((256, 256), Image.Resampling.LANCZOS)
    pixels = np.asarray(sample)
    height, width = pixels.shape[:2]
    lightness = pixels.min(axis=2)
    border = np.concatenate((lightness[:4].ravel(), lightness[-4:].ravel(),
                             lightness[:, :4].ravel(), lightness[:, -4:].ravel()))
    tight_margin = float(np.mean(border >= 244)) < .95
    if tight_margin and not has_white_frame(photo):
        return None

    strong = Image.fromarray(np.uint8(lightness < 228) * 255).filter(ImageFilter.MaxFilter(3))
    boxes = components(np.asarray(strong) > 0)
    if not boxes or not any((right-left) >= .23 * (bottom-top) for left, top, right, bottom in boxes):
        return None
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[2] for box in boxes)
    y1 = max(box[3] for box in boxes)
    if min(x0, y0, width-x1, height-y1) < 2 and not tight_margin and not has_white_frame(photo):
        return None
    if (y1-y0) < height * .25 or (x1-x0) < width * .10:
        return None

    # Grow through pale phone edges when they touch the detected subject.
    weak = lightness < 247
    while x0 > 2 and np.count_nonzero(weak[y0:y1, x0-1]) > (y1-y0) * .09:
        x0 -= 1
    while x1 < width-2 and np.count_nonzero(weak[y0:y1, x1]) > (y1-y0) * .09:
        x1 += 1
    while y0 > 2 and np.count_nonzero(weak[y0-1, x0:x1]) > (x1-x0) * .09:
        y0 -= 1
    while y1 < height-2 and np.count_nonzero(weak[y1, x0:x1]) > (x1-x0) * .09:
        y1 += 1

    box = (max(0, (x0-2)*photo.width//width), max(0, (y0-2)*photo.height//height),
           min(photo.width, (x1+2)*photo.width//width), min(photo.height, (y1+2)*photo.height//height))
    crop = photo.crop(box)
    ratio = (x1-x0)/(y1-y0)
    if ratio < .17 or ratio > 1.9:
        return None
    crop_pixels = pixels[y0:y1, x0:x1].astype(np.float32)
    subject_height, subject_width = crop_pixels.shape[:2]
    middle = crop_pixels[int(subject_height * .24):int(subject_height * .77)]
    spreads = []
    coverage = []
    for start, end in ((.10, .30), (.55, .83)):
        patch = middle[:, int(subject_width*start):int(subject_width*end)]
        spreads.append(float(np.max(np.quantile(patch, .90, axis=(0, 1)) -
                                    np.quantile(patch, .10, axis=(0, 1)))))
        coverage.append(float(np.mean(patch.min(axis=2) < 247)))
    # A full-width, abrupt bottom edge is typical of a cropped detail photo.
    dark = lightness[y0:y1, x0:x1] < 228
    profile = dark.mean(axis=1)
    filled = profile[profile > .1]
    blunt_base = filled.size >= 3 and bool(np.min(filled[-3:]) > .96)
    quieter, busier = sorted(spreads)
    screen_contrast = busier >= 33 and busier - quieter >= 21 and (
        quieter <= 58 or (busier >= 2.2 * quieter and busier - quieter >= 45))
    panels = split_phone_views(crop_pixels) if .60 <= ratio <= 1.15 and not blunt_base else None
    tone_match = .65 <= ratio <= .93 and screen_contrast and min(coverage) >= .68
    if panels is not None and has_front_and_back(panels):
        view = FRONT_AND_BACK
    elif panels is not None or (tone_match and not blunt_base):
        view = POSSIBLE_PAIR
    elif .24 <= ratio <= .57 and not blunt_base:
        view = SINGLE_VIEW
    else:
        view = UNCONFIRMED_VIEW

    # 单面或构图不明：直接舍弃，不导出。
    if view not in KEEP_VIEWS:
        return None
    if require_product and view == UNCONFIRMED_VIEW and not has_phone_outline(crop_pixels):
        return None

    overlap = watermark_may_overlap(photo, box)
    # 按 good case 比例缩放主体高度：留白过大则放大，过小则缩小。
    target_h = max(1, round(SIZE * SUBJECT_HEIGHT_RATIO))
    scale = target_h / crop.height
    new_w = max(1, round(crop.width * scale))
    new_h = target_h
    if new_w > SIZE:
        scale = SIZE / crop.width
        new_w = SIZE
        new_h = max(1, round(crop.height * scale))
    crop = crop.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (SIZE, SIZE), "white")
    canvas.paste(crop, ((SIZE - crop.width) // 2, (SIZE - crop.height) // 2))
    canvas.save(destination, quality=95, subsampling=0)
    signature = np.asarray(canvas.resize((64, 64), Image.Resampling.LANCZOS), dtype=np.int16)
    notes = []
    if view == POSSIBLE_PAIR:
        notes.append("请核对是否为正背同框")
    if overlap:
        notes.append("水印可能与主体重叠，请检查成图")
    metadata = {"view":view, "review":bool(notes), "watermarkReview":overlap, "note":"；".join(notes),
                "pixels":(box[2]-box[0])*(box[3]-box[1]), "tone":round(max(spreads), 2)}
    return metadata, signature


def looks_identical(first, second):
    difference = np.abs(first - second)
    return float(difference.mean()) < 1.4 and float((difference.max(axis=2) > 18).mean()) < .03
