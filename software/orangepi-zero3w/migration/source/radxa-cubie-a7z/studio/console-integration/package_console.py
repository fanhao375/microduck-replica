#!/usr/bin/env python3
"""Export the reviewed web delta and build a deterministic, installer-checked bundle."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
DEFAULT_RELEASE = '20261109-console1'

# Share the installer's path and manifest rules instead of maintaining a second schema.
_spec = importlib.util.spec_from_file_location('console_installer', HERE / 'install_console.py')
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def load_baseline(path):
    installer.require(not Path(path).is_symlink(), 'baseline links are forbidden')
    raw = installer.parse_json(Path(path).read_bytes(), 'invalid baseline JSON')
    installer.require(isinstance(raw, dict) and raw, 'baseline is empty')
    result = {}
    for name, checksum in raw.items():
        installer.require(isinstance(name, str) and name.startswith('web/'), 'baseline paths must start with web/')
        relative = name[4:]
        parts = relative.split('/')
        installer.require(all(part and part not in ('.', '..') and not part.startswith('.')
                              and ':' not in part and '\\' not in part for part in parts), 'invalid baseline path')
        installer.require(isinstance(checksum, str) and installer.SHA.fullmatch(checksum), 'invalid baseline checksum')
        result[relative] = checksum
    return result


def read_tree(root, baseline=None):
    """Validate names before reading; unexpected credentials or generated files fail closed."""
    root = installer.real_directory(root)
    result = {}
    for parent, directories, leaves in os.walk(root, followlinks=False):
        for name in directories:
            path = Path(parent) / name
            installer.require(not path.is_symlink() and path.is_dir() and not name.startswith('.'),
                              'unexpected web directory: ' + str(path.relative_to(root)))
        for name in leaves:
            relative = (Path(parent) / name).relative_to(root).as_posix()
            if baseline is None or relative not in baseline:
                installer.relative_name(relative)
            data, _ = installer.read_regular(root, relative)
            result[relative] = digest(data)
    return result


def write_file(path, data):
    path = Path(path)
    installer.require(not path.is_symlink(), 'output links are forbidden')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.console-', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def replace_tree(destination, contents):
    """Replace only a generated overlay or bundle; a failed write leaves the old tree intact."""
    destination = Path(destination)
    installer.require(not destination.is_symlink(), 'output directory links are forbidden')
    installer.require(not destination.exists() or destination.is_dir(), 'output is not a directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.console-tree-', dir=destination.parent) as temporary:
        temporary = Path(temporary)
        stage = temporary / 'next'
        stage.mkdir()
        for name, data in sorted(contents.items()):
            write_file(stage / name, data)
        previous = temporary / 'previous'
        if destination.exists():
            os.replace(destination, previous)
        try:
            os.replace(stage, destination)
        except BaseException:
            if previous.exists():
                os.replace(previous, destination)
            raise


def export_workspace(workspace, overlay, baseline_path, deletion_path):
    workspace = installer.real_directory(workspace)
    for name, expected in installer.BACKEND_BASELINE.items():
        data, _ = installer.read_regular(workspace, name)
        installer.require(digest(data) == expected, 'A7Z backend baseline differs: ' + name)
    source_baseline = workspace.parent / 'baseline-web.json'
    baseline = load_baseline(source_baseline)
    current = read_tree(workspace / 'web', baseline)
    changed = sorted(name for name in baseline.keys() | current.keys() if baseline.get(name) != current.get(name))
    contents, deleted = {}, []
    for name in changed:
        # In particular, model/** and model sidecars can never enter an export.
        installer.relative_name(name)
        if name not in current:
            deleted.append(name)
            continue
        data, _ = installer.read_regular(workspace / 'web', name)
        installer.require(digest(data) == current[name], 'working web changed during export: ' + name)
        contents[name] = data
    installer.require(read_tree(workspace / 'web', baseline) == current, 'working web changed during export; retry')
    installer.require(contents or deleted, 'working web has no frontend changes')
    replace_tree(overlay, contents)
    write_file(baseline_path, json_bytes({'web/' + name: value for name, value in baseline.items()}))
    # The overlay is intentionally sparse, so deletions need an explicit versioned list.
    if deleted:
        write_file(deletion_path, json_bytes(deleted))
    else:
        installer.require(not Path(deletion_path).is_symlink(), 'deletion list links are forbidden')
        Path(deletion_path).unlink(missing_ok=True)
    return len(contents), len(deleted)


def load_deletions(path, baseline):
    path = Path(path)
    if not path.exists():
        return []
    installer.require(not path.is_symlink(), 'deletion list links are forbidden')
    deleted = installer.parse_json(path.read_bytes(), 'invalid deletion JSON')
    installer.require(isinstance(deleted, list) and all(isinstance(name, str) for name in deleted)
                      and len(deleted) == len(set(deleted)), 'invalid deletion list')
    for name in deleted:
        installer.relative_name(name)
        installer.require(name in baseline, 'deleted file is absent from baseline: ' + name)
    return sorted(deleted)


def deterministic_archive(path, contents):
    """Ignore machine ownership, timestamps, paths and gzip filename metadata."""
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0, compresslevel=9) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(contents.items()):
                member = tarfile.TarInfo('bundle/' + name)
                member.size, member.mode, member.mtime = len(data), 0o644, 0
                member.uid = member.gid = 0
                member.uname = member.gname = ''
                archive.addfile(member, io.BytesIO(data))
    data = stream.getvalue()
    write_file(path, data)
    write_file(str(path) + '.sha256', (digest(data) + '  ' + Path(path).name + '\n').encode())
    return digest(data)


def build_bundle(overlay, baseline_path, deletion_path, artifacts, release):
    baseline = load_baseline(baseline_path)
    current = read_tree(overlay)
    deleted = load_deletions(deletion_path, baseline)
    installer.require(not current.keys() & set(deleted), 'a file is both present and deleted')
    changes = {name: {'before_sha256': baseline.get(name), 'after_sha256': checksum}
               for name, checksum in current.items()}
    changes.update({name: {'before_sha256': baseline[name], 'after_sha256': None} for name in deleted})
    manifest = installer.validate_manifest({'schema': 1, 'kind': 'console-web', 'release': release,
        'compression': 'remove-changed-variants', 'files': dict(sorted(changes.items()))})
    contents = {'manifest.json': json_bytes(manifest)}
    for name, expected in sorted(current.items()):
        data, _ = installer.read_regular(overlay, name)
        installer.require(digest(data) == expected, 'versioned overlay changed while packaging: ' + name)
        contents['payload/web/' + name] = data
    artifacts = Path(artifacts)
    installer.require(not artifacts.is_symlink(), 'artifact directory links are forbidden')
    bundle = artifacts / 'bundle'
    replace_tree(bundle, contents)
    installer.load_manifest(bundle)  # Check every payload digest with the production installer.
    archive = artifacts / ('console-web-' + release + '.tar.gz')
    checksum = deterministic_archive(archive, contents)
    installer_data, _ = installer.read_regular(HERE, 'install_console.py')
    installer_path = artifacts / 'install_console.py'
    write_file(installer_path, installer_data)
    write_file(str(installer_path) + '.sha256', (digest(installer_data) + '  install_console.py\n').encode())
    return {'release': release, 'bundle': str(bundle), 'archive': str(archive), 'sha256': checksum,
            'installer': str(installer_path), 'installer_sha256': digest(installer_data),
            'files': len(changes), 'new': sum(row['before_sha256'] is None for row in changes.values()),
            'deleted': len(deleted), 'payload_bytes': sum(len(data) for name, data in contents.items()
                                                       if name.startswith('payload/'))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='store_true', help='replace versioned web/ with the working frontend delta first')
    parser.add_argument('--workspace', type=Path, default=HERE / 'workspace/studio')
    parser.add_argument('--artifacts', type=Path, default=HERE / 'artifacts')
    parser.add_argument('--release', default=DEFAULT_RELEASE)
    args = parser.parse_args(argv)
    # Reject an invalid release before modifying an existing export.
    installer.require(isinstance(args.release, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', args.release),
                      'invalid release identifier')
    overlay, baseline, deleted = HERE / 'web', HERE / 'baseline-web.json', HERE / 'deleted-web.json'
    if args.export:
        export_workspace(args.workspace, overlay, baseline, deleted)
    print(json.dumps(build_bundle(overlay, baseline, deleted, args.artifacts, args.release), sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (installer.InstallError, OSError, ValueError, tarfile.TarError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        raise SystemExit(1)
