#!/usr/bin/env python3
"""Export committed application source only; never package local credentials or data."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('destination', type=Path)
parser.add_argument('--commit', default='HEAD')
a = parser.parse_args()
root = Path(__file__).resolve().parents[1]
commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--verify', a.commit + '^{commit}'], text=True).strip()
paths = ['app', 'Dockerfile', 'Dockerfile.white-api', 'Dockerfile.coverage-api', 'requirements.txt', 'requirements.white-api.txt', 'requirements.coverage-api.txt', 'requirements.lock.txt', '.dockerignore'] + [f'Dockerfile.{m}-api' for m in ('governance','douyin','integration','main-image')] + [f'requirements.{m}-api.txt' for m in ('governance','douyin','integration','main-image')] + ['dev/main-image-service/server.py', 'dev/main-image-service/catalog.py', 'dev/main-image-service/engine.py', 'dev/main-image-service/sample_config.py', 'dev/main-image-service/store.py', 'dev/main-image-service/tool']
if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--', *paths], text=True).strip():
    raise SystemExit('Application/build files have uncommitted changes; commit/review first')
archive = subprocess.check_output(['git', '-C', str(root), 'archive', '--format=tar', commit, '--', *paths])
a.destination = a.destination.resolve()
a.destination.mkdir(parents=True, exist_ok=False)
files = {}
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    for item in tar:
        rel = Path(item.name)
        if item.isdir(): continue
        if not item.isfile() or rel.is_absolute() or '..' in rel.parts:
            raise SystemExit('Only ordinary source files can be packaged')
        if any(x.startswith('.') and x != '.dockerignore' for x in rel.parts) or any(x in {'runtime', 'gaia_runtime', '__pycache__'} for x in rel.parts) or rel.suffix in {'.db', '.sqlite', '.pem', '.key', '.pyc'}:
            raise SystemExit('Refusing data/credential/cache file: ' + str(rel))
        data = tar.extractfile(item).read()
        p = a.destination / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(item.mode & 0o777)
        files[rel.as_posix()] = hashlib.sha256(data).hexdigest()
(a.destination / 'release-source.json').write_text(json.dumps({'commit': commit, 'files': files}, indent=2) + '\n')
print(json.dumps({'commit': commit, 'files': len(files), 'destination': str(a.destination)}))
