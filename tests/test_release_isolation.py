import importlib.util
import json
from pathlib import Path
import hashlib
import pytest

spec = importlib.util.spec_from_file_location('gallery_release', Path(__file__).parents[1] / 'deploy/release.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
OLD = 'sha256:' + '1' * 64
NEW = 'sha256:' + '2' * 64
BASE = 'python@sha256:' + '3' * 64


class FakeDocker:
    def __init__(self):
        self.state = {s: {'container': s + '-old', 'image': OLD, 'health': 'healthy'} for s in r.SERVICES}
        self.calls = []
        self.fail = False
    def snapshot(self): return json.loads(json.dumps(self.state))
    def build(self, module, source, tag, base):
        self.calls.append(('build', module, str(source), tag, base))
        return NEW
    def image_id(self, image): return image
    def switch(self, module, image, override):
        self.calls.append(('switch', module, image))
        if self.fail and image == NEW: raise RuntimeError('synthetic health failure')
        self.state[module] = {'container': module + '-' + image[-4:], 'image': image, 'health': 'healthy'}


def source(tmp_path):
    p = tmp_path / 'source'
    p.mkdir()
    data = b'FROM python\n'
    (p / 'Dockerfile').write_bytes(data)
    (p / 'release-source.json').write_text(json.dumps({'commit': 'a' * 40, 'files': {'Dockerfile': hashlib.sha256(data).hexdigest()}}))
    return p


@pytest.mark.parametrize('module', list(r.SERVICES))
def test_publish_and_rollback_only_target(tmp_path, module):
    docker = FakeDocker()
    before = docker.snapshot()
    src = source(tmp_path)
    result = r.apply_backend(tmp_path / 'host', module, OLD, 'test-release', src, BASE, docker=docker)
    assert result['status'] == 'deployed_unverified'
    assert result['ui_verified'] is False
    assert result['image'] == NEW
    for other in r.SERVICES:
        if other != module: assert docker.state[other] == before[other]
    rollback = r.apply_backend(tmp_path / 'host', module, NEW, 'test-rollback', rollback_image=OLD, docker=docker)
    assert rollback['image'] == OLD
    assert all(c[1] == module for c in docker.calls)
    assert '/release-state/backend/' in docker.calls[0][2]


def test_stale_target_never_builds_or_switches(tmp_path):
    docker = FakeDocker()
    with pytest.raises(ValueError, match='Stale baseline'):
        r.apply_backend(tmp_path, 'coverage-api', NEW, 'stale', docker=docker)
    assert docker.calls == []


def test_failed_switch_restores_target_only(tmp_path):
    docker = FakeDocker()
    docker.fail = True
    before = docker.snapshot()
    with pytest.raises(RuntimeError, match='synthetic'):
        r.apply_backend(tmp_path / 'host', 'coverage-api', OLD, 'failure', source(tmp_path), BASE, docker=docker)
    record = json.loads((tmp_path / 'host/release-state/backend/coverage-api/failure/release.json').read_text())
    assert record['status'] == 'failed_restored'
    assert docker.state['coverage-api']['image'] == OLD
    assert docker.state['gallery'] == before['gallery']
    assert docker.state['white-gallery-api'] == before['white-gallery-api']


def test_source_tampering_fails_before_build(tmp_path):
    docker = FakeDocker()
    src = source(tmp_path)
    (src / 'Dockerfile').write_text('changed')
    with pytest.raises(ValueError, match='Source changed'):
        r.apply_backend(tmp_path / 'host', 'gallery', OLD, 'tamper', src, BASE, docker=docker)
    assert docker.calls == []


def test_failed_restore_is_explicit(tmp_path):
    class BrokenDocker(FakeDocker):
        def switch(self, *args): raise RuntimeError('all switches fail')
    with pytest.raises(RuntimeError):
        r.apply_backend(tmp_path / 'host', 'gallery', OLD, 'broken', source(tmp_path), BASE, docker=BrokenDocker())
    record = json.loads((tmp_path / 'host/release-state/backend/gallery/broken/release.json').read_text())
    assert record['status'] == 'failed_restore_required'


def test_service_lock_prevents_second_publisher(tmp_path):
    with r.locked(tmp_path / 'release-state/locks/gallery.lock'):
        with pytest.raises(BlockingIOError):
            r.apply_backend(tmp_path, 'gallery', OLD, 'locked', docker=FakeDocker())


def package(tmp_path, version):
    p = tmp_path / ('package-' + version)
    p.mkdir()
    (p / 'module.json').write_text(json.dumps({'id': 'coverage', 'version': version, 'title': '', 'subtitle': '', 'nav_label': '', 'icon': ''}))
    for f in ('index.html', 'module.js', 'module.css'): (p / f).write_text(version)
    return p


def test_frontend_rollback_checks_baseline_and_keeps_other_module(tmp_path):
    root = tmp_path / 'modules'
    other = root / 'white-gallery'
    other.mkdir(parents=True)
    (other / 'untouched').write_text('keep')
    r.frontend(root, 'coverage', '1.0', 'none', package(tmp_path, '1.0'))
    r.frontend(root, 'coverage', '1.1', '1.0', package(tmp_path, '1.1'))
    with pytest.raises(ValueError, match='Stale baseline'):
        r.frontend(root, 'coverage', '1.0', '1.0', rollback=True)
    assert (root / 'coverage/current').resolve().name == '1.1'
    r.frontend(root, 'coverage', '1.0', '1.1', rollback=True)
    assert (root / 'coverage/current/module.js').read_text() == '1.0'
    assert (other / 'untouched').read_text() == 'keep'


def test_docker_switch_uses_exact_image_and_no_dependency_restart(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(r, 'run', lambda args: calls.append(args) or '')
    d = r.Docker(tmp_path)
    d.switch('coverage-api', NEW, tmp_path / 'target.json')
    assert json.loads((tmp_path / 'target.json').read_text()) == {'services': {'coverage-api': {'image': NEW}}}
    cmd = calls[-1]
    assert cmd[-1] == 'coverage-api'
    assert '--no-deps' in cmd and '--no-build' in cmd and '--wait' in cmd
    assert 'stop' not in cmd and 'rm' not in cmd


def test_clean_coverage_image_explicitly_includes_parameter_dependencies():
    dockerfile = (Path(__file__).parents[1] / "Dockerfile.coverage-api").read_text()
    assert "app/assets_api.py" in dockerfile
    assert "app/categories.py" in dockerfile
    assert "COPY app/static/model_params.json" in dockerfile
    assert 'RUN python -c "import app.coverage_service"' in dockerfile
