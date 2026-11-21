"""Bake a signed stable software update into a copy of the public ZERO 3W 1104 image."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'updater'))
from bootstrap import profile, verify_tree
from control import Engine
from package import unpack_outer

BASE = 'f9970ec551bcddbeeae9748b0ce38f36450fa1497618c07920723849f975aa78'
PREP = '41c5988ea4f711b106743b9538ff6fea221841aae06105ade00853c194279f07'
KEY = '0878853a92b10c9196c5b63e02087664923c576e6bc2e064d3bbdcc1be056bf5'
STOCK = '2026.11.4'
IMAGE = 'microduck-replica-20261104.img.xz'
HOST = 'usr/local/lib/microduck-updater'
SUITE = 'opt/microduck/software'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


@contextmanager
def mounted(raw, root, *, readonly):
    args = ['losetup', '--find', '--show', '--partscan']
    if readonly:
        args.append('--read-only')
    loop = subprocess.check_output([*args, raw], text=True).strip()
    try:
        require(re.fullmatch(r'/dev/loop[0-9]+', loop), 'invalid loop device')
        part = loop + 'p1'
        run('mount', '-t', 'ext4', '-o', 'ro,noload' if readonly else 'rw', part, root)
        try:
            yield
        finally:
            run('umount', root)
        run('e2fsck', '-f', '-n', part)
    finally:
        run('losetup', '-d', loop)


def checked_prep(root, prepare):
    key = root / 'etc/microduck-updater/trusted_keys/release.pub'
    require(sha(key) == KEY and sha(prepare) == PREP, 'unexpected stock trust key or preparation tool')
    run('minisign', '-V', '-m', prepare, '-x', str(prepare) + '.minisig', '-p', key)
    # Invoke the verified helper's offline filesystem operations. Its CLI is for live robots.
    with zipfile.ZipFile(prepare) as archive:
        api = {'__name__': 'factory_preparation'}
        exec(compile(archive.read('__main__.py'), str(prepare), 'exec'), api)
        files = api['payload'](archive)
    return api, files


def install(root, work, package, prepare, version):
    api, files = checked_prep(root, prepare)
    require(api['check_stock'](root, json.loads(files['manifest.json'])) == STOCK, 'expected factory software')
    api['stage'](root, files, mode='prepare')
    cache = work / 'package'
    with package.open('rb') as f:
        manifest = unpack_outer(f, cache)
    require(manifest['version'] == version, 'package version differs from release tag')
    key = root / 'etc/microduck-updater/trusted_keys/release.pub'
    for file in [cache / (version + '.manifest.json'), cache / manifest['url']]:
        run('minisign', '-V', '-m', file, '-p', key)
    require(sha(cache / manifest['url']) == manifest['sha256'], 'signed payload digest mismatch')
    config = work / 'offline.toml'
    config.write_text(profile(root))
    qemu = shutil.which('qemu-aarch64') or shutil.which('qemu-aarch64-static')
    require(qemu, 'qemu-user is required')
    command = [qemu, '-L', root, root / HOST / 'updaterd', '--config', config,
               '--robot-socket', work / 'absent.sock', '--token', work / 'unused.json']
    env = dict(os.environ, MICRODUCK_OFFLINE_BUILD='1',
               PATH=str(root / HOST / 'engine-bin') + ':' + os.environ['PATH'])
    os.umask(0o022)
    with tempfile.TemporaryDirectory(prefix='duck-engine-') as ipc:
        sock = Path(ipc) / 'engine.sock'
        proc = subprocess.Popen([*command, '--socket', sock], env=env)
        try:
            for _ in range(200):
                if sock.exists():
                    break
                require(proc.poll() is None, 'factory engine exited before opening socket')
                time.sleep(0.05)
            engine = Engine(str(sock))
            for dry, outcome in ((True, 'dry_run_passed'), (False, 'applied')):
                result = engine.call('update.apply', {'component': 'software', 'target': {'exact': version},
                                     'options': {'from_dir': str(cache), 'dry_run': dry}})
                require(result['outcome'] == outcome, 'factory update failed: ' + str(result))
        finally:
            proc.terminate()
            proc.wait(timeout=15)
    return manifest


def verify(root, prepare, version, manifest):
    api, files = checked_prep(root, prepare)
    require('已完整暂存' in api['stage'](root, files, mode='check'), 'online host service overrides missing')
    suite = root / SUITE
    require(os.readlink(suite / 'current') == 'releases/' + version, 'wrong active release')
    require((suite / 'releases' / STOCK).is_dir(), 'factory rollback version is missing')
    release = (suite / 'current').resolve()
    meta = verify_tree(release)
    require(meta['version'] == version and meta['source_revision'] == manifest['source_revision'],
            'installed metadata differs from signed release')
    for path in [release, *release.rglob('*')]:
        s = path.lstat()
        require(s.st_uid == 0 and not path.is_symlink() and bool(s.st_mode & stat.S_IROTH)
                and (not path.is_dir() or bool(s.st_mode & stat.S_IXOTH)), 'runtime permissions: ' + str(path))
    history = [json.loads(x) for x in (root / 'var/lib/microduck-updater/engine/update-log.jsonl').read_text().splitlines()]
    require(history[-1]['to'] == version and history[-1]['outcome']['kind'] == 'success', 'engine state mismatch')
    require(not (root / 'etc/machine-id').read_bytes().strip(), 'populated machine identity')
    require(not any((root / 'etc/ssh').glob('ssh_host_*key*')), 'populated SSH identity')
    require(not any((root / 'etc/NetworkManager/system-connections').glob('*')), 'populated Wi-Fi settings')
    require('--commissioning' in (root / 'etc/systemd/system/robotd.service.d/zzzz-zz-microduck-commissioning.conf').read_text(),
            'missing commissioning configuration')
    components = json.loads((root / 'usr/share/doc/microduck/software-components.json').read_text())
    for name, digest in components['controller_bundle']['components'].items():
        require(sha(root / 'opt/robot/feetech-ft5-r5' / name) == digest, 'factory controller changed: ' + name)
    return len(meta['files'])


def build(base, package, prepare, version, out, work):
    require(os.geteuid() == 0, 'run as root on Linux')
    require(re.fullmatch(r'[1-9][0-9]*\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', version),
            'expected a stable three-part release version')
    require(sha(base) == BASE, 'incorrect original 1104 image')
    require(package.name == version + '.software-update.tar', 'incorrect software package name')
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    require(not any(work.iterdir()) and not any(out.iterdir()), 'work and output must be empty')
    root = work / 'root'
    root.mkdir()
    raw = work / 'disk.img'
    with raw.open('xb') as f:
        run('xz', '-dc', '-T4', base, stdout=f)
    package_hash = sha(package)
    with mounted(raw, root, readonly=False):
        manifest = install(root, work, package, prepare, version)
        factory = {'schema': 1, 'base_version': '1104', 'software_version': version,
                   'source_revision': manifest['source_revision'], 'base_sha256': BASE,
                   'package_sha256': package_hash, 'prepare_sha256': PREP}
        dump(root / 'usr/share/doc/microduck/factory-preload.json', factory)
    # Read back the distributable into an independent raw file before inspecting its filesystem.
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        stage = Path(tmp)
        image = stage / IMAGE
        with image.open('xb') as f:
            run('xz', '-3', '-T4', '-c', raw, stdout=f)
        delivered = work / 'delivered.img'
        with delivered.open('xb') as f:
            run('xz', '-T4', '-dc', image, stdout=f)
        require(sha(delivered) == sha(raw), 'compressed image readback mismatch')
        with mounted(delivered, root, readonly=True):
            count = verify(root, prepare, version, manifest)
            require(json.loads((root / 'usr/share/doc/microduck/factory-preload.json').read_text()) == factory,
                    'factory preload record mismatch')
            run('unshare', '--mount', '--net', '--pid', '--fork', sys.executable, Path(__file__).with_name('smoke.py'),
                root, work, version)
        digest = sha(image)
        dump(stage / '1104-build.json', dict(factory, image_sha256=digest, image_bytes=image.stat().st_size,
             verified_runtime_files=count, compressed_image_readback=True, ext4_checked=True,
             arm_studio_http=True, stale_browser_revalidation=True, hardware_boot_verified=False))
        (stage / (IMAGE + '.sha256')).write_text(f'{digest}  {IMAGE}\n')
        for path in stage.iterdir():
            path.rename(out / path.name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for opt in ('base', 'package', 'prepare', 'out', 'work'):
        parser.add_argument('--' + opt, type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument('--version', required=True)
    build(**vars(parser.parse_args()))
