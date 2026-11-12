#!/usr/bin/env python3
"""Build a small reviewed Studio update from the existing A7Z source export."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    before = json.loads((HERE / 'tracking-baseline.json').read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    payload = args.output / 'payload'
    payload.mkdir()
    for relative, expected in before.items():
        source = args.source / relative
        if digest(source) != expected:
            raise SystemExit(f'Studio source differs from reviewed baseline: {relative}')
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    subprocess.run(['patch', '--batch', '--forward', '-p1', '-i', str(HERE / 'a7z-leg-tracking.patch')],
                   cwd=payload, check=True)
    for source in (HERE / 'tracking').rglob('*'):
        if source.is_file() and '__pycache__' not in source.parts:
            target = payload / source.relative_to(HERE / 'tracking')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    diagnostics = HERE.parent / 'control/diagnostics'
    shutil.copyfile(diagnostics / 'leg_tracking_probe.py', payload / 'studio/leg_tracking_probe.py')
    analyzer = (diagnostics / 'analyze_leg_tracking.py').read_text()
    needle = 'from leg_tracking_probe import DEG_PER_RAW'
    if analyzer.count(needle) != 1:
        raise SystemExit('analyzer import changed; review package layout')
    analyzer = analyzer.replace(needle, 'from .leg_tracking_probe import DEG_PER_RAW')
    (payload / 'studio/analyze_leg_tracking.py').write_text(analyzer)
    files = {}
    for path in payload.rglob('*'):
        if path.is_file():
            relative = str(path.relative_to(payload))
            if path.suffix == '.py':
                compile(path.read_text(), relative, 'exec')
            files[relative] = {'before_sha256': before.get(relative), 'after_sha256': digest(path)}
    expected_files = set(before) | {'studio/leg_tracking.py', 'studio/leg_tracking_probe.py',
                                    'studio/analyze_leg_tracking.py', 'web/leg-tracking.js'}
    if set(files) != expected_files:
        raise SystemExit('unexpected tracking payload files; review overlay')
    (args.output / 'manifest.json').write_text(json.dumps({'schema': 1, 'files': files}, indent=2) + '\n')
    shutil.copyfile(HERE / 'install_tracking.py', args.output / 'install_tracking.py')
    archive = args.output.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as out:
        for path in sorted(args.output.rglob('*')):
            if path.is_file():
                out.add(path, arcname=str(path.relative_to(args.output)), recursive=False)
    archive.with_suffix(archive.suffix + '.sha256').write_text(f'{digest(archive)}  {archive.name}\n')
    print(json.dumps({'archive': str(archive), 'sha256': digest(archive), 'files': sorted(files)}))


if __name__ == '__main__':
    main()
