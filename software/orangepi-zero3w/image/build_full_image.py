#!/usr/bin/env python3
"""Build a preinstalled, credential-free Orange Pi image in a disposable Docker VM.

Only the build work directory and reviewed sources are mounted into the ARM64
container. Device operations target a loop device created from /work/rootfs.ext4.
No host device is passed through and no installed image service is started.
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import uuid

from image_fs import (OFFICIAL_SHA256, OFFSET, assemble, check, children, debug,
                      digest, extract, quote, read, tool)
from build_public_image import (rebuild_journal, sanitize_filesystem,
                                scan_removed_credentials, zero_free_space)

HERE = Path(__file__).resolve().parent
NAME = 'microduck-orangepi-zero3w-studio-20261112-r2'
BUILDER = 'python:3.11-slim'


def runtime_filter(member, destination):
    """Apply Python's safe data filter while preserving verified runtime file modes."""
    path = Path(member.name)
    if (path.is_absolute() or '..' in path.parts or not path.parts
            or path.parts[0] != 'orangepi-quanzhi'
            or not (member.isfile() or member.isdir() or member.issym())):
        raise ValueError('Unexpected runtime archive member')
    filtered = tarfile.data_filter(member, destination)
    return filtered.replace(mode=member.mode & 0o777)


def safe_extract_runtime(archive, destination):
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, 'r:gz') as source:
        source.extractall(destination, filter=runtime_filter)


def validate_report(report, manifest_sha256):
    if (report.get('status') != 'preinstalled-and-offline-readback-verified'
            or report.get('payload_manifest_sha256') != manifest_sha256
            or report.get('file_hash_mode_and_root_ownership_verified') is not True
            or report.get('studio_state_empty') is not True
            or report.get('policy_enabled') is not False
            or report.get('robotd_mode') != 'read_only'
            or report.get('services_started') is not False):
        raise ValueError('Preinstallation report does not match the verified runtime')


# Guest scripts use fixed container paths; user paths never enter shell source.
GUEST = r'''#!/bin/sh
set -eu
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    sh /work/cleanup-guest.sh || status=1
    exit "$status"
}
trap cleanup EXIT HUP INT TERM
export DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1
printf '[image-build] Installing Linux build tools\n'
apt-get update -qq > /work/builder-packages.log 2>&1
apt-get install -y --no-install-recommends e2fsprogs util-linux mount >> /work/builder-packages.log 2>&1
printf '[image-build] Extracting runtime on native Linux storage\n'
python3 -B /work/extract-runtime.py
printf '[image-build] Checking and mounting the offline rootfs\n'
e2fsck -fn /work/rootfs.ext4 > /work/filesystem-before-resize.txt 2>&1
if [ ! -f /work/prior-preinstall-report.json ]; then
    # resize2fs requires a recorded full check, not just the preceding read-only
    # check. Accept only healthy/repaired exit statuses, never unresolved errors.
    set +e
    e2fsck -f -p /work/rootfs.ext4 > /work/filesystem-force-check.txt 2>&1
    check_status=$?
    set -e
    case "$check_status" in 0|1) ;; *) exit "$check_status" ;; esac
    resize2fs /work/rootfs.ext4 > /work/filesystem-resize.txt 2>&1
fi
e2fsck -fn /work/rootfs.ext4 > /work/filesystem-after-resize.txt 2>&1
mkdir -p /mnt/image-root
losetup --find --show /work/rootfs.ext4 > /run/microduck-loop-device
mount "$(cat /run/microduck-loop-device)" /mnt/image-root
# On Linux, detaching a mounted loop marks it AUTOCLEAR. It remains usable until
# this mount closes, including if Docker has to destroy the mount namespace.
losetup --detach "$(cat /run/microduck-loop-device)"
if [ -f /work/prior-preinstall-report.json ]; then
    printf '[image-build] Independently checking the prepared checkpoint\n'
    python3 -B /source/image/preinstall_runtime.py --root /mnt/image-root \
        --bundle /build-bundle/orangepi-quanzhi --report /work/preinstall-report.json --verify-only
else
    printf '[image-build] Installing image dependencies and provisioning\n'
    sh /source/image/prepare_rootfs.sh /mnt/image-root /source > /work/dependency-install.log 2>&1
    printf '[image-build] Preinstalling and reading back the complete runtime\n'
    python3 -B /source/image/preinstall_runtime.py --root /mnt/image-root \
        --bundle /build-bundle/orangepi-quanzhi --report /work/preinstall-report.json
fi
chroot /mnt/image-root /usr/bin/dpkg-query -W '-f=${binary:Package}\t${Version}\n' > /work/packages.tsv
printf '[image-build] Unmounting the verified rootfs\n'
sync
'''
CLEANUP = r'''#!/bin/sh
set -eu
# Lazy unmount is only a failure fallback in this disposable mount namespace.
for mount in /mnt/image-root/proc /mnt/image-root/dev /mnt/image-root; do
    if mountpoint -q "$mount"; then
        umount "$mount" || umount -l "$mount"
    fi
done
if [ -f /run/microduck-loop-device ]; then
    device=$(cat /run/microduck-loop-device)
    case "${device#/dev/loop}" in ''|*[!0-9]*) exit 1 ;; esac
    if losetup "$device" >/dev/null 2>&1; then losetup --detach "$device"; fi
    rm /run/microduck-loop-device
fi
'''
EXTRACTOR = r'''import hashlib, importlib.util, json, sys
from pathlib import Path
sys.path.insert(0, '/source/image')
import build_full_image
archive = Path('/work/runtime.tar.gz')
expected = Path('/work/runtime-sha256.txt').read_text().strip()
if build_full_image.digest(archive) != expected:
    raise ValueError('Runtime archive checksum changed in transfer')
build_full_image.safe_extract_runtime(archive, Path('/build-bundle'))
manifest = Path('/build-bundle/orangepi-quanzhi/manifest.json')
manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
Path('/work/runtime-manifest-sha256.txt').write_text(manifest_sha + '\n')
prior = Path('/work/prior-preinstall-report.json')
if prior.exists():
    build_full_image.validate_report(json.loads(prior.read_text()), manifest_sha)
'''


def logged(command, logfile, *, timeout=None):
    with logfile.open('a') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
        try:
            for line in process.stdout:
                log.write(line)
                log.flush()
                if line.startswith('[image-build]'):
                    print(line.rstrip(), flush=True)
            status = process.wait(timeout=timeout)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if status:
        raise RuntimeError(f'{Path(command[0]).name} failed; inspect {logfile.name}')


def docker_available():
    executable = shutil.which('docker')
    if not executable:
        raise RuntimeError('Docker CLI is unavailable')
    override = os.environ.get('DOCKER_HOST')
    if override and not override.startswith('unix://'):
        raise ValueError('Use a local Docker socket; remote Docker hosts are not supported')
    if not override or os.environ.get('DOCKER_CONTEXT'):
        result = subprocess.run([executable, 'context', 'inspect', '--format',
                                 '{{.Endpoints.docker.Host}}'], capture_output=True, text=True, timeout=20)
        if result.returncode or not result.stdout.strip().startswith('unix://'):
            raise RuntimeError('Expected a local Docker context using a Unix socket')
    result = subprocess.run([executable, 'info', '--format', '{{.ServerVersion}}'],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Docker daemon is unavailable; start local Docker first')
    return executable


def build_linux_rootfs(docker, source, work):
    for script, text in [('build-guest.sh', GUEST), ('cleanup-guest.sh', CLEANUP),
                         ('extract-runtime.py', EXTRACTOR)]:
        (work / script).write_text(text)
    name = 'microduck-image-' + uuid.uuid4().hex[:12]
    log = work / 'docker-build.log'
    for path in (source, work):
        if any(character in str(path) for character in ',\n\r'):
            raise ValueError('Docker bind-mount paths cannot contain commas or line breaks')
    try:
        logged([docker, 'create', '--name', name, '--platform', 'linux/arm64', '--privileged',
                '--mount', f'type=bind,src={work},dst=/work',
                '--mount', f'type=bind,src={source},dst=/source,readonly',
                '--env', 'PYTHONDONTWRITEBYTECODE=1', BUILDER,
                '/bin/sh', '-c', 'exec sleep infinity'], log)
        logged([docker, 'start', name], log)
        logged([docker, 'exec', name, '/bin/sh', '/work/build-guest.sh'], log)
    finally:
        # The guest has its own EXIT trap; this handles interrupted Docker exec.
        try:
            cleanup = subprocess.run([docker, 'exec', name, '/bin/sh', '/work/cleanup-guest.sh'],
                                     capture_output=True, text=True, timeout=45)
            (work / 'docker-cleanup.log').write_text(cleanup.stdout + cleanup.stderr)
        finally:
            removed = subprocess.run([docker, 'rm', '--force', name], capture_output=True,
                                     text=True, timeout=45)
        if removed.returncode and 'No such container' not in removed.stderr:
            raise RuntimeError('Could not remove the disposable Docker builder')
    manifest_sha = (work / 'runtime-manifest-sha256.txt').read_text().strip()
    report = json.loads((work / 'preinstall-report.json').read_text())
    validate_report(report, manifest_sha)
    return report


def verify_sanitized_filesystem(fs, preinstall):
    """Read critical installed files and setup state directly from the cleaned ext4."""
    content = read(fs, '/var/lib/orangepi-migration/installed-manifest.json')
    if hashlib.sha256(content).hexdigest() != preinstall['payload_manifest_sha256']:
        raise RuntimeError('Installed manifest changed during image cleanup')
    manifest = json.loads(content)
    if len(manifest['files']) != preinstall['payload_files_verified']:
        raise RuntimeError('Installed payload count changed during image cleanup')
    critical = {
        'opt/robot/feetech-ft5-r5/robotd', 'opt/microduck-studio/app/run.py',
        'usr/local/sbin/microduck-helper', 'etc/robot/feetech-ft5/robotd.toml',
        'etc/robot/feetech-ft5/hd1910-calibration.toml',
        'etc/systemd/system/robotd.service', 'etc/systemd/system/microduck-studio.service',
    }
    if not critical.issubset({record['path'] for record in manifest['files']}):
        raise RuntimeError('Installed manifest lacks a required runtime file')
    checked = 0
    for record in manifest['files']:
        if record['path'] in critical or record['path'].endswith('/policy.onnx'):
            if hashlib.sha256(read(fs, '/' + record['path'])).hexdigest() != record['sha256']:
                raise RuntimeError('Runtime changed during image cleanup: ' + record['path'])
            checked += 1
    for directory in ('/var/lib/microduck-studio', '/var/log/microduck-studio'):
        if children(fs, directory):
            raise RuntimeError('Studio credentials or logs exist after cleanup')
        metadata = debug(fs, 'stat ' + quote(directory))
        for field, expected in [(rb'Mode:\s+([0-7]+)', 0o700),
                                (rb'User:\s+(\d+)', preinstall['accounts']['uid']),
                                (rb'Group:\s+(\d+)', preinstall['accounts']['gid'])]:
            match = re.search(field, metadata)
            if not match or int(match[1], 8 if field.startswith(b'Mode') else 10) != expected:
                raise RuntimeError('Studio state ownership/mode changed during cleanup')
    expected_links = {
        '/usr/local/bin/robotctl': '/opt/robot/feetech-ft5-r5/robotctl',
        **{'/etc/systemd/system/multi-user.target.wants/' + name:
           '/etc/systemd/system/' + name for name in preinstall['enabled_services']},
    }
    for path, expected in expected_links.items():
        metadata = debug(fs, 'stat ' + quote(path))
        match = re.search(rb'Fast link dest: "([^"]+)"', metadata)
        if not match or match[1].decode() != expected:
            raise RuntimeError('Runtime startup link changed during cleanup')
    return {'post_cleanup_installed_manifest_verified': True,
            'post_cleanup_runtime_file_hashes_verified': checked,
            'post_cleanup_empty_studio_state_owner_mode_and_enable_links_verified': True}


def build(base, runtime, runtime_sha256, output, size_gib=6, checkpoint=None, prior_report=None):
    for path in (base, runtime, *([checkpoint, prior_report] if checkpoint else [])):
        if not path.is_file() or path.is_symlink():
            raise ValueError('Inputs must be regular non-symlink files')
    if not re.fullmatch(r'[0-9a-f]{64}', runtime_sha256):
        raise ValueError('Expected a lowercase SHA-256 for the runtime archive')
    if digest(base) != OFFICIAL_SHA256 or digest(runtime) != runtime_sha256:
        raise ValueError('Official image or runtime archive checksum differs')
    image_bytes = size_gib * 1024 ** 3
    if image_bytes < base.stat().st_size or image_bytes % 512 or image_bytes > 2 ** 32 * 512:
        raise ValueError('Requested disk size must fit the official image and use whole sectors')
    # Check tools and Docker before allocating or changing any output files.
    for name in ('e2fsck', 'debugfs', 'dumpe2fs', 'tune2fs'):
        tool(name)
    docker = docker_available()
    output.mkdir(parents=True, exist_ok=False)
    work = output / 'work'
    work.mkdir(mode=0o700)
    fs = work / 'rootfs.ext4'
    print('[image-build] Preparing a private rootfs copy', flush=True)
    if checkpoint:
        shutil.copyfile(checkpoint, fs)
        shutil.copyfile(prior_report, work / 'prior-preinstall-report.json')
        if fs.stat().st_size != image_bytes - OFFSET:
            raise ValueError('Checkpoint size differs from the requested image size')
    else:
        extract(base, fs)
        with fs.open('r+b') as stream:
            stream.truncate(image_bytes - OFFSET)
    shutil.copyfile(runtime, work / 'runtime.tar.gz')
    if digest(work / 'runtime.tar.gz') != runtime_sha256:
        raise RuntimeError('Runtime archive copy verification failed')
    (work / 'runtime-sha256.txt').write_text(runtime_sha256 + '\n')
    preinstall = build_linux_rootfs(docker, HERE.parent, work)
    print('[image-build] Cleaning credentials and all device-specific state', flush=True)
    cleanup, needles = sanitize_filesystem(fs, work)
    report = {'name': NAME, 'kind': 'Preinstalled Studio, read-only robot runtime and Wi-Fi provisioning',
              'official_base_sha256': OFFICIAL_SHA256, 'runtime_archive_sha256': runtime_sha256,
              'disk_bytes': image_bytes, 'preinstallation': preinstall,
              'docker_builder_image': BUILDER, 'linux_platform': 'linux/arm64',
              'device_operations': 'Only the loop device created from this build rootfs',
              'first_boot_installation_required': False,
              'physical_board_boot_test': 'not-performed', 'browser_test': 'not-performed'}
    report.update(cleanup)
    report.update(verify_sanitized_filesystem(fs, preinstall))
    report.update(rebuild_journal(fs))
    report.update(zero_free_space(fs))
    check(fs, output / 'filesystem-after.txt')
    image = output / (NAME + '.img')
    print('[image-build] Assembling and scanning the complete disk image', flush=True)
    assemble(base, fs, image)
    if needles:
        report.update(scan_removed_credentials(image, needles))
    else:
        report['credential_scan'] = 'No credential payloads were present in the supplied checkpoint'
    archive = output / (NAME + '.img.gz')
    print('[image-build] Compressing the verified image', flush=True)
    with image.open('rb') as source, archive.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=6) as target:
            shutil.copyfileobj(source, target, 8 * 1024 * 1024)
    report['files'] = [{'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)}
                       for path in (image, archive)]
    for source, name in [(work / 'preinstall-report.json', 'preinstall-report.json'),
                         (work / 'packages.tsv', 'packages.tsv')]:
        shutil.copyfile(source, output / name)
    (output / 'image-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (output / 'IMG-SHA256SUMS').write_text(''.join(f"{item['sha256']}  {item['name']}\n" for item in report['files']))
    print(json.dumps({'name': NAME, 'files': report['files'], 'board_boot_test': 'not-performed'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', type=Path, required=True)
    parser.add_argument('--runtime-archive', type=Path, required=True)
    parser.add_argument('--runtime-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--size-gib', type=int, default=6)
    parser.add_argument('--finalize-rootfs', type=Path)
    parser.add_argument('--preinstall-report', type=Path)
    args = parser.parse_args()
    if bool(args.finalize_rootfs) != bool(args.preinstall_report):
        parser.error('--finalize-rootfs and --preinstall-report must be supplied together')
    build(args.base_image.absolute(), args.runtime_archive.absolute(), args.runtime_sha256,
          args.output_dir.absolute(), args.size_gib,
          args.finalize_rootfs.absolute() if args.finalize_rootfs else None,
          args.preinstall_report.absolute() if args.preinstall_report else None)
