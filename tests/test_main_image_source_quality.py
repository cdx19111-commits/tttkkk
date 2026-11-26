"""Unit checks for source orientation filtering and unique model keys."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "dev" / "main-image-service"
sys.path.insert(0, str(SERVICE))

from catalog import orientation_ok, target_key  # noqa: E402


def test_phone_orientation_rejects_side_view_and_flat_lay():
    assert not orientation_ok("phone", (1495, 206))  # flat-lay strip
    assert not orientation_ok("phone", (2190, 198))  # side view
    assert not orientation_ok("phone", (920, 813))   # two phones / landscape
    assert orientation_ok("phone", (784, 1602))      # normal portrait phone
    assert orientation_ok("phone", (514, 681))       # foldable portrait


def test_tablet_allows_landscape_product():
    assert orientation_ok("tablet", (638, 480))
    assert orientation_ok("tablet", (480, 638))
    assert not orientation_ok("tablet", (1400, 200))


def test_model_key_includes_brand():
    assert target_key("oneplus", "11") == "oneplus:11"
    assert target_key("iqoo", "11") == "iqoo:11"
    assert target_key("", "11") == "11"
