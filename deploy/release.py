#!/usr/bin/env python3
"""Target-only application releases. No database or credential file operations."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SERVICES = {
    'gallery': ('Dockerfile', None, 'gallery-gateway'),
    'white-gallery-api': ('Dockerfile.white-api', 'deploy/white-gallery-api.compose.yaml', 'gallery-white-api'),
    'coverage-api': ('Dockerfile.coverage-api', 'deploy/coverage-api.compose.yaml', 'gallery-coverage-api'),
}
IMAGE = re.compile(r'^sha256:[a-f0-9]{64}$')
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')

def validate_name(value):
    if not NAME.fullmatch(value):
        raise ValueError('Invalid name/version')
    return value

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

@contextlib.contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def event(root, module, data):
    now = datetime.now(timezone.utc)
    payload = {'time': now.isoformat(), 'module': module, **data}
    atomic_json(root / module / (now.strftime('%Y%m%dT%H%M%S.%f') + '.json'), payload)


def run(args):
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True)
    if result.returncode:
        # Do not dump Compose environment/config or application logs.
        raise RuntimeError('Command failed: ' + str(args[0]) + ' (exit ' + str(result.returncode) + ')')
    return result.stdout.strip()


class Docker:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def compose(self, module, override=None):
        args = ['docker', 'compose', '--project-directory', str(self.root), '-p', 'gallery-mvp', '-f', str(self.root / 'compose.yaml')]
        extra = SERVICES[module][1]
        if extra: args += ['-f', str(self.root / extra)]
        if override: args += ['-f', str(override)]
        return args

    def snapshot(self):
        result = {}
        for module in SERVICES:
            cid = run(self.compose(module) + ['ps', '-q', module])
            if not cid: raise RuntimeError('Missing running service: ' + module)
            data = json.loads(run(['docker', 'inspect', '--format', '{"Id":{{json .Id}},"Image":{{json .Image}},"State":{"Health":{"Status":{{json .State.Health.Status}}}}}', cid]))
            # Keep only nonsensitive runtime identity, never persist environment values.
            result[module] = {'container': data['Id'], 'image': data['Image'], 'health': data.get('State', {}).get('Health', {}).get('Status', 'unknown')}
        return result

    def image_id(self, image):
        return run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])

    def build(self, module, source, tag, base, offline=False):
        args = ['docker', 'build', '--build-arg', 'BASE_IMAGE=' + base, '-f', str(source / SERVICES[module][0]), '-t', tag]
        env = dict(os.environ)
        if offline:
            # Legacy builder accepts exact local image IDs without registry resolution.
            env['DOCKER_BUILDKIT'] = '0'
            args += ['--network=none', '--build-arg', 'PIP_OFFLINE=1']
        result = subprocess.run(args + [str(source)], text=True, capture_output=True, env=env, timeout=300)
        if result.returncode:
            raise RuntimeError('Target image build failed (exit ' + str(result.returncode) + '); running services unchanged')
        return self.image_id(tag)

    def switch(self, module, image, override):
        # A single service image override: shared runtime mounts/ports stay in existing Compose.
        atomic_json(override, {'services': {module: {'image': image}}})
        run(self.compose(module, override) + ['up', '-d', '--no-deps', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '90', module])


def assert_others_unchanged(before, after, target):
    for module in SERVICES:
        if module != target and before[module] != after[module]:
            raise RuntimeError('Non-target service changed: ' + module + '; do not overwrite it')


def apply_backend(root, module, expected, release_id, source=None, base=None, rollback_image=None, docker=None, offline=False):
    validate_name(release_id)
    if not IMAGE.fullmatch(expected): raise ValueError('Expected image must be an exact sha256 image ID')
    if rollback_image is not None and not IMAGE.fullmatch(rollback_image): raise ValueError('Rollback requires an exact image ID')
    root = Path(root).resolve()
    docker = docker or Docker(root)
    state = root / 'release-state'
    with locked(state / 'locks' / (module + '.lock')):
        before = docker.snapshot()
        old = before[module]['image']
        if old != expected: raise ValueError('Stale baseline: running target image changed; integrate before publishing')
        if any(v['health'] != 'healthy' for v in before.values()): raise ValueError('Services must be healthy before release')
        release_dir = state / 'backend' / module / release_id
        release_dir.mkdir(parents=True, exist_ok=False)
        record = {'module': module, 'release': release_id, 'previous_image': old, 'before': before, 'status': 'preparing', 'ui_verified': False}
        record_path = release_dir / 'release.json'
        atomic_json(record_path, record)
        switching = False
        try:
            if rollback_image is None:
                source = Path(source).resolve()
                manifest = json.loads((source / 'release-source.json').read_text())
                if not re.fullmatch(r'[a-f0-9]{40}', manifest.get('commit', '')): raise ValueError('Missing source commit')
                verify_source(source, manifest)
                if not re.fullmatch(r'(?:[a-zA-Z0-9./:_-]+@)?sha256:[a-f0-9]{64}', base or ''): raise ValueError('Base image must be pinned by digest')
                # An isolated copy prevents two upload/build operations sharing a mutable source tree.
                frozen = release_dir / 'source'
                shutil.copytree(source, frozen)
                verify_source(frozen, manifest)
                record['commit'] = manifest['commit']
                record['base_image'] = base
                record['offline_build'] = offline
                image = docker.build(module, frozen, SERVICES[module][2] + ':' + release_id, base, offline=offline)
            else:
                image = docker.image_id(rollback_image)
            if not IMAGE.fullmatch(image): raise ValueError('Invalid resolved image ID')
            now = docker.snapshot()
            if now[module] != before[module]: raise ValueError('Target changed during build; refusing switch')
            record.update(image=image, status='switching')
            atomic_json(record_path, record)
            switching = True
            docker.switch(module, image, release_dir / 'target.compose.json')
            after = docker.snapshot()
            if after[module]['image'] != image or after[module]['health'] != 'healthy': raise RuntimeError('Target health/version mismatch')
            assert_others_unchanged(before, after, module)
            record.update(status='deployed_unverified', after=after)
            atomic_json(record_path, record)
            atomic_json(state / 'backend' / module / 'current.json', record)
            event(state / 'events', module, {'action': 'rollback' if rollback_image else 'publish', 'release': release_id, 'previous_image': old, 'image': image, 'status': record['status']})
            return record
        except Exception:
            record['status'] = 'failed_before_switch'
            if switching:
                try:
                    # The module lock protects target rollback; no other service is stopped or restored.
                    docker.switch(module, old, release_dir / 'restore.compose.json')
                    restored = docker.snapshot()
                    if restored[module]['image'] != old or restored[module]['health'] != 'healthy': raise RuntimeError('Restore failed')
                    record['status'] = 'failed_restored'
                    record['after'] = restored
                    atomic_json(state / 'backend' / module / 'current.json', record)
                except Exception:
                    record['status'] = 'failed_restore_required'
            atomic_json(record_path, record)
            event(state / 'events', module, {'action': 'failure', 'release': release_id, 'status': record['status']})
            raise


def verify_source(source, manifest):
    if any(p.is_symlink() for p in source.rglob('*')): raise ValueError('Source symlinks are forbidden')
    files = manifest.get('files', {})
    if not files: raise ValueError('Empty source manifest')
    for rel, digest in files.items():
        p = source / rel
        if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()) or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError('Source changed: ' + rel)
    actual = {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file() and p.name != 'release-source.json'}
    if actual != set(files): raise ValueError('Unexpected source files')


def frontend(root, module, version, expected, package=None, rollback=False):
    for value in (module, version, expected): validate_name(value)
    root = Path(root).resolve()
    module_root = root / module
    with locked(module_root / '.publish.lock'):
        current = module_root / 'current'
        previous = os.readlink(current) if current.is_symlink() else None
        if current.exists() and not current.is_symlink(): raise ValueError('Invalid current pointer')
        if previous and (not re.fullmatch(r'versions/[A-Za-z0-9][A-Za-z0-9._-]{0,63}', previous) or not current.is_dir()): raise ValueError('Invalid current pointer')
        if (previous or 'none') != ('none' if expected == 'none' else 'versions/' + expected): raise ValueError('Stale baseline: frontend target changed')
        destination = module_root / 'versions' / version
        destination.parent.mkdir(parents=True, exist_ok=True)
        if rollback:
            if destination.is_symlink() or not destination.is_dir(): raise ValueError('Rollback version not found')
        else:
            if destination.exists() or destination.is_symlink(): raise ValueError('Version already exists; use a new version')
            package = Path(package).resolve()
            metadata = json.loads((package / 'module.json').read_text())
            if metadata.get('id') != module or metadata.get('version') != version: raise ValueError('Module identity mismatch')
            for key in ('title', 'subtitle', 'nav_label', 'icon'):
                if not isinstance(metadata.get(key), str) or len(metadata[key]) > 120: raise ValueError('Invalid module metadata')
            if not isinstance(metadata.get('order', 100), int): raise ValueError('Invalid module order')
            stage = Path(tempfile.mkdtemp(dir=module_root, prefix='.staging-'))
            try:
                for name in ('module.json', 'index.html', 'module.js', 'module.css'):
                    if (package / name).is_symlink(): raise ValueError('Package symlinks are forbidden')
                    shutil.copy2(package / name, stage / name)
                os.rename(stage, destination)
            finally:
                if stage.exists(): shutil.rmtree(stage)
        metadata = json.loads((destination / 'module.json').read_text())
        if metadata.get('id') != module or metadata.get('version') != version: raise ValueError('Rollback metadata mismatch')
        link = module_root / ('.current-' + str(os.getpid()))
        try:
            link.symlink_to('versions/' + version)
            os.replace(link, current)
        finally:
            if link.is_symlink(): link.unlink()
        try:
            event(root / '.release-events', module, {'action': 'rollback' if rollback else 'publish', 'previous': previous, 'version': version, 'status': 'deployed_unverified'})
        except Exception:
            # Logging failure must not leave an unrecorded new pointer active.
            if previous:
                link.symlink_to(previous)
                os.replace(link, current)
            else: current.unlink()
            raise
        return {'module': module, 'version': version, 'previous': previous, 'status': 'deployed_unverified'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    for action in ('backend-publish', 'backend-rollback'):
        p = subs.add_parser(action)
        p.add_argument('module', choices=SERVICES)
        p.add_argument('--root', default='/opt/gallery-mvp')
        p.add_argument('--expected-image', required=True)
        p.add_argument('--release', required=True)
        if action == 'backend-publish':
            p.add_argument('--source', required=True)
            p.add_argument('--base-image', required=True)
            p.add_argument('--offline', action='store_true')
        else: p.add_argument('--image', required=True)
    for action in ('frontend-publish', 'frontend-rollback'):
        p = subs.add_parser(action)
        p.add_argument('module')
        p.add_argument('version')
        p.add_argument('--expected-current', required=True)
        p.add_argument('--root', default=os.environ.get('FRONTEND_MODULES_DIR', '/opt/gallery-mvp/frontend-modules'))
        if action == 'frontend-publish': p.add_argument('--package', required=True)
    a = parser.parse_args()
    if a.action.startswith('backend'):
        result = apply_backend(a.root, a.module, a.expected_image, a.release, source=getattr(a, 'source', None), base=getattr(a, 'base_image', None), rollback_image=getattr(a, 'image', None), offline=getattr(a, 'offline', False))
        print(json.dumps({k: result.get(k) for k in ('module', 'release', 'status', 'previous_image', 'image', 'commit')}, ensure_ascii=False))
    else:
        print(json.dumps(frontend(a.root, a.module, a.version, a.expected_current, getattr(a, 'package', None), a.action == 'frontend-rollback'), ensure_ascii=False))

if __name__ == '__main__':
    try: main()
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
