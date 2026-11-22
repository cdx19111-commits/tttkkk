import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "publish-frontend-module.sh"


def make_package(path: Path, version: str):
    path.mkdir()
    (path / "module.json").write_text(json.dumps({
        "id": "sample", "version": version, "title": "样例",
        "subtitle": "测试", "nav_label": "样例", "icon": "◇", "order": 1,
    }), encoding="utf-8")
    for filename in ("index.html", "module.js", "module.css"):
        (path / filename).write_text("test", encoding="utf-8")


def publish(tmp_path, package, version, expected="none"):
    env = {**os.environ, "FRONTEND_MODULES_DIR": str(tmp_path / "published")}
    return subprocess.run(
        [str(SCRIPT), "sample", version, str(package), expected],
        env=env, text=True, capture_output=True,
    )


def test_publisher_switches_current_atomically_and_keeps_previous_version(tmp_path):
    package1 = tmp_path / "package1"
    package2 = tmp_path / "package2"
    make_package(package1, "1.0.0")
    make_package(package2, "1.1.0")

    first = publish(tmp_path, package1, "1.0.0")
    second = publish(tmp_path, package2, "1.1.0", "1.0.0")
    module_root = tmp_path / "published" / "sample"

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert (module_root / "current").resolve().name == "1.1.0"
    assert (module_root / "versions" / "1.0.0" / "module.js").is_file()
    assert "versions/1.0.0" in second.stdout


def test_publisher_refuses_overwriting_an_existing_version(tmp_path):
    package = tmp_path / "package"
    make_package(package, "1.0.0")
    assert publish(tmp_path, package, "1.0.0").returncode == 0

    result = publish(tmp_path, package, "1.0.0", "1.0.0")

    assert result.returncode != 0
    assert "Version already exists" in result.stderr


def test_stale_baseline_cannot_replace_a_newer_release(tmp_path):
    first = tmp_path / "first"
    stale = tmp_path / "stale"
    make_package(first, "1.0.0")
    make_package(stale, "1.1.0")
    assert publish(tmp_path, first, "1.0.0").returncode == 0
    result = publish(tmp_path, stale, "1.1.0", "none")
    assert result.returncode != 0
    assert "Stale baseline" in result.stderr
    root = tmp_path / "published" / "sample"
    assert (root / "current").resolve().name == "1.0.0"
    assert not (root / "versions" / "1.1.0").exists()


def test_publishing_one_module_preserves_another(tmp_path):
    package = tmp_path / "package"
    make_package(package, "1.0.0")
    other = tmp_path / "published" / "other"
    (other / "versions" / "2.0.0").mkdir(parents=True)
    (other / "versions" / "2.0.0" / "module.js").write_text("keep")
    (other / "current").symlink_to("versions/2.0.0")
    assert publish(tmp_path, package, "1.0.0").returncode == 0
    assert os.readlink(other / "current") == "versions/2.0.0"
    assert (other / "current" / "module.js").read_text() == "keep"
