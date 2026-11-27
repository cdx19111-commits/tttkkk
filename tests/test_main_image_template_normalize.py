"""Template uploads accept any square 800–3000 image and normalize to 1080."""
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dev" / "main-image-service"))

import server  # noqa: E402


def save_image(path: Path, size, mode="RGBA", color=(255, 0, 0, 255), fmt=None):
    Image.new(mode, size, color).save(path, format=fmt)
    return path


def test_800_square_is_upscaled_to_1080_and_keeps_alpha(tmp_path):
    path = tmp_path / "template-800.png"
    image = Image.new("RGBA", (800, 800), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (0, 0, 400, 800))
    image.save(path)

    result = server.normalize_template_image(path)

    assert result.size == (1080, 1080)
    assert result.mode == "RGBA"
    assert result.getpixel((5, 5))[3] == 255
    assert result.getpixel((1000, 500))[3] == 0


@pytest.mark.parametrize("size", [(1080, 1080), (1200, 1200)])
def test_square_sizes_become_1080(tmp_path, size):
    path = save_image(tmp_path / f"template-{size[0]}.png", size)

    result = server.normalize_template_image(path)

    assert result.size == (1080, 1080)
    assert result.mode == "RGBA"


def test_jpeg_and_webp_are_converted_to_png_canvas(tmp_path):
    jpeg = save_image(tmp_path / "template.jpg", (900, 900), mode="RGB",
                      color=(20, 40, 60), fmt="JPEG")
    webp = save_image(tmp_path / "template.webp", (1000, 1000), mode="RGB",
                      color=(20, 40, 60), fmt="WEBP")

    assert server.normalize_template_image(jpeg).size == (1080, 1080)
    assert server.normalize_template_image(webp).size == (1080, 1080)


@pytest.mark.parametrize("size", [(1200, 800), (800, 1200)])
def test_non_square_is_rejected(tmp_path, size):
    path = save_image(tmp_path / "non-square.png", size)
    with pytest.raises(ValueError, match="正方形"):
        server.normalize_template_image(path)


@pytest.mark.parametrize("size", [(799, 799), (3001, 3001)])
def test_out_of_range_sizes_are_rejected(tmp_path, size):
    path = save_image(tmp_path / "out-of-range.png", size, mode="L", color=0)
    with pytest.raises(ValueError):
        server.normalize_template_image(path)


def test_unsupported_format_is_rejected(tmp_path):
    path = save_image(tmp_path / "template.bmp", (800, 800), mode="RGB",
                      color=(0, 0, 0), fmt="BMP")
    with pytest.raises(ValueError, match="PNG"):
        server.normalize_template_image(path)


def test_animated_template_is_rejected(tmp_path):
    path = tmp_path / "animated.gif"
    frames = [Image.new("P", (800, 800), color) for color in (0, 1)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    with pytest.raises(ValueError, match="动图"):
        server.normalize_template_image(path)
