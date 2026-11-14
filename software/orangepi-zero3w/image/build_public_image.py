#!/usr/bin/env python3
"""Build a credential-free Orange Pi Zero 3W Wi-Fi provisioning image.

Only an exact official base image is accepted. No live board is accessed.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

from image_fs import (OFFICIAL_SHA256, assemble, check, children, debug, digest,
                      exists, extract, quote, read, remove, run, tool, write)

NAME = 'microduck-orangepi-zero3w-provision-20261112'
KEY_PATHS = [f'/etc/ssh/ssh_host_{kind}_key' for kind in ('rsa', 'ecdsa', 'ed25519')]
SSH_BLOCK = '''\t# SSH Keys creation
\trm -f /etc/ssh/ssh_host*
\tread entropy_before </proc/sys/kernel/random/entropy_avail
\tdpkg-reconfigure openssh-server >/dev/null 2>&1
\tservice sshd restart
\tread entropy_after </proc/sys/kernel/random/entropy_avail
\techo -e "\\n### [firstrun] Recreated SSH keys (entropy: ${entropy_before} ${entropy_after})" >>${Log}
'''
IDENTITY_UNIT = b'''[Unit]
Description=Create unique SSH host keys for this Orange Pi installation
Before=ssh.service ssh.socket

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A
RemainAfterExit=yes
'''
SSH_DEPENDENCY = b'''[Unit]
Requires=orangepi-image-identity.service
After=orangepi-image-identity.service
'''


def ranges(value):
    for item in value.split(','):
        item = item.strip()
        if item:
            ends = list(map(int, item.split('-')))
            yield ends[0], ends[-1]


def zero_free_space(fs):
    """Erase deleted data, including free inode slots, using e2fsprogs' map."""
    listing = run([tool('dumpe2fs'), fs]).decode()
    block_size = int(re.search(r'^Block size:\s*(\d+)', listing, re.M)[1])
    inode_size = int(re.search(r'^Inode size:\s*(\d+)', listing, re.M)[1])
    per_group = int(re.search(r'^Inodes per group:\s*(\d+)', listing, re.M)[1])
    expected = int(re.search(r'^Free blocks:\s*(\d+)', listing, re.M)[1])
    total = 0
    zero = b'\0' * (1024 * 1024)
    with fs.open('r+b') as stream:
        for group in re.split(r'\nGroup \d+:', listing)[1:]:
            free = re.search(r'^  Free blocks:(.*)$', group, re.M)
            for start, end in ranges(free[1]):
                stream.seek(start * block_size)
                remaining = (end - start + 1) * block_size
                total += end - start + 1
                while remaining:
                    amount = min(remaining, len(zero))
                    stream.write(zero[:amount])
                    remaining -= amount
            table = re.search(r'Inode table at (\d+)', group)
            free_inodes = re.search(r'^  Free inodes:(.*)$', group, re.M)
            for start, end in ranges(free_inodes[1]):
                stream.seek(int(table[1]) * block_size + ((start - 1) % per_group) * inode_size)
                stream.write(b'\0' * ((end - start + 1) * inode_size))
    if total != expected:
        raise RuntimeError('Free block map was not fully processed')
    return {'free_blocks_zeroed': total, 'block_size': block_size,
            'unused_inode_slots_zeroed': True}


def journal_data_layout(fs):
    """Map journal logical data blocks without including extent-tree metadata."""
    stats = debug(fs, 'stats')
    inode = debug(fs, 'stat <8>')
    block_size_match = re.search(rb'^Block size:\s*(\d+)', stats, re.M)
    block_count_match = re.search(rb'^Block count:\s*(\d+)', stats, re.M)
    size_match = re.search(rb'^User:.*?\bSize:\s*(\d+)\s*$', inode, re.M)
    if not all((block_size_match, block_count_match, size_match)) or b'\nEXTENTS:\n' not in inode:
        raise RuntimeError('Cannot establish journal extent geometry')
    block_size, block_count = int(block_size_match[1]), int(block_count_match[1])
    inode_bytes = int(size_match[1])
    if (block_size < 1024 or block_size & (block_size - 1)
            or fs.stat().st_size != block_size * block_count
            or inode_bytes == 0 or inode_bytes % block_size):
        raise RuntimeError('Journal size or filesystem block geometry is inconsistent')
    expected = inode_bytes // block_size
    mapping = re.sub(rb'\s+', b'', inode.split(b'\nEXTENTS:\n', 1)[1])
    extents = []
    metadata = set()
    for token in mapping.split(b','):
        tree = re.fullmatch(rb'\(ETB\d+\):(\d+)', token)
        if tree:
            block = int(tree[1])
            if block in metadata or not 0 < block < block_count:
                raise RuntimeError('Invalid journal extent-tree metadata block')
            metadata.add(block)
            continue
        extent = re.fullmatch(rb'\((\d+)(?:-(\d+))?\):(\d+)(?:-(\d+))?', token)
        if not extent:
            raise RuntimeError('Unsupported journal extent mapping')
        logical_start, logical_end = int(extent[1]), int(extent[2] or extent[1])
        physical_start, physical_end = int(extent[3]), int(extent[4] or extent[3])
        if (logical_end < logical_start or physical_end < physical_start
                or logical_end - logical_start != physical_end - physical_start
                or not 0 < physical_start <= physical_end < block_count):
            raise RuntimeError('Invalid journal data extent')
        extents.append((logical_start, logical_end, physical_start, physical_end))
    data = []
    for logical_start, logical_end, physical_start, physical_end in sorted(extents):
        if logical_start != len(data):
            raise RuntimeError('Journal logical data contains a hole or overlap')
        data.extend(range(physical_start, physical_end + 1))
    if len(data) != expected or len(set(data)) != expected or set(data) & metadata:
        raise RuntimeError('Journal data size, uniqueness or metadata separation failed')
    allocated = list(map(int, debug(fs, 'blocks <8>').split()))
    if (len(set(allocated)) != len(allocated)
            or set(allocated) != set(data) | metadata):
        raise RuntimeError('Journal extent data disagrees with its allocated block list')
    first = debug(fs, 'bmap <8> 0').strip()
    if not re.fullmatch(rb'\d+', first) or int(first) != data[0]:
        raise RuntimeError('Journal logical zero disagrees with bmap')
    return data, metadata, block_size


def rebuild_journal(fs):
    run([tool('tune2fs'), '-O', '^has_journal', fs])
    run([tool('tune2fs'), '-j', fs])
    blocks, metadata, block_size = journal_data_layout(fs)
    # The first `blocks <8>` entry may be an extent-tree node. Only logical data
    # block zero contains JBD2; preserve every extent-tree metadata block.
    with fs.open('r+b') as stream:
        stream.seek(blocks[0] * block_size)
        header = stream.read(1024)
        if (len(header) != 1024 or struct.unpack_from('>II', header) != (0xc03b3998, 4)
                or struct.unpack_from('>II', header, 12) != (block_size, len(blocks))
                or struct.unpack_from('>I', header, 28)[0] != 0):
            raise RuntimeError('New journal superblock is inconsistent or not clean')
        for block in blocks:
            stream.seek(block * block_size)
            stream.write(header + b'\0' * (block_size - 1024) if block == blocks[0] else b'\0' * block_size)
    return {'journal_recreated': True, 'journal_blocks_cleared': len(blocks),
            'journal_extent_metadata_blocks_preserved': len(metadata)}


def scan_removed_credentials(image, needles):
    scanner = shutil.which('rg')
    if scanner:
        # Fixed-string multi-pattern matching is exact and avoids expensive
        # backtracking across large disk images. Sensitive needles stay in RAM.
        result = subprocess.run(
            [scanner, '--text', '--quiet', '--fixed-strings', '--file', '-', str(image)],
            input=b'\n'.join(sorted(needles)) + b'\n', capture_output=True,
        )
        if result.returncode != 1:
            raise RuntimeError('Credential scan matched material or failed')
    else:
        pattern = re.compile(b'|'.join(re.escape(n) for n in needles))
        overlap = max(map(len, needles)) - 1
        tail = b''
        with image.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                content = tail + block
                if pattern.search(content):
                    raise RuntimeError('Original credential material survives in output bytes')
                tail = content[-overlap:]
    return {'original_key_payloads_and_password_hashes_absent': True,
            'credential_needle_count': len(needles), 'full_disk_bytes_scanned': image.stat().st_size}


def sanitize_filesystem(fs, work):
    """Clean an offline prepared rootfs, preserving application state ownership.

    The caller installs packages and payloads before this step, then rebuilds the
    journal, erases free space, checks the filesystem, and scans returned needles.
    No live board or host credentials are accessed.
    """
    needles = set()

    def collect_private_material(path, mode=None):
        if not exists(fs, path):
            return
        if mode is None:
            metadata = debug(fs, 'stat ' + quote(path))
            if b'Type: directory' in metadata:
                mode = 0o040000
            elif b'Type: regular' in metadata:
                mode = 0o100000
            else:
                return
        kind = mode & 0o170000
        if kind == 0o040000:
            for child, child_mode in children(fs, path):
                collect_private_material(child, child_mode)
        elif kind == 0o100000:
            content = read(fs, path)
            if Path(path).name in ('shadow-', 'gshadow-'):
                for line in content.decode().splitlines():
                    fields = line.split(':')
                    if len(fields) > 1 and fields[1].startswith('$'):
                        needles.add(fields[1].encode())
            for match in re.finditer(
                    rb'-----BEGIN [^-]*PRIVATE KEY-----\s+(.*?)-----END [^-]*PRIVATE KEY-----',
                    content, re.S):
                needles.update(line for line in match[1].splitlines()
                               if re.fullmatch(rb'[A-Za-z0-9+/=]{32,}', line))

    def clear_directory(path):
        if not exists(fs, path):
            return
        if b'Type: directory' not in debug(fs, 'stat ' + quote(path)):
            raise ValueError('Expected an actual state directory: ' + path)
        # Only remove children. Keep owner, group and mode set by preinstallation.
        for child, mode in children(fs, path):
            collect_private_material(child, mode)
            remove(fs, child)

    for path, mode in children(fs, '/etc/ssh'):
        if Path(path).name.startswith('ssh_host_'):
            collect_private_material(path, mode)
            remove(fs, path)
    collect_private_material('/usr/share/doc/openvpn/examples')
    remove(fs, '/usr/share/doc/openvpn/examples')
    for database in ['/etc/shadow', '/etc/gshadow']:
        if not exists(fs, database):
            raise ValueError('Expected account credential database')
        records = []
        for line in read(fs, database).decode().splitlines():
            fields = line.split(':')
            if len(fields) < 2:
                raise ValueError('Malformed account credential database')
            if fields[1].startswith('$'):
                needles.add(fields[1].encode())
            fields[1] = '!'
            records.append(':'.join(fields))
        write(fs, database, ('\n'.join(records) + '\n').encode(), work, mode=0o640, gid=42)
    for path in ['/etc/shadow-', '/etc/gshadow-', '/etc/security/opasswd',
                 '/var/lib/systemd/random-seed', '/var/lib/urandom/random-seed',
                 '/boot/orangepi_first_run.txt', '/boot/orangepi_first_run.txt.old']:
        collect_private_material(path)
        remove(fs, path)
    state_directories = [
        '/etc/ssl/private', '/etc/NetworkManager/system-connections',
        '/var/lib/NetworkManager', '/var/lib/orangepi-provision',
        '/var/lib/microduck-studio', '/var/log/microduck-studio',
        '/var/lib/robot', '/var/lib/microduck-helper',
        '/var/cache/apt/archives', '/var/lib/apt/lists', '/run', '/tmp', '/var/tmp',
    ]
    for directory in state_directories:
        clear_directory(directory)
    helper_work = '/var/lib/microduck-helper/imu-recalibrate'
    if exists(fs, '/var/lib/microduck-helper'):
        debug(fs, 'mkdir ' + quote(helper_work), write=True)
        for field, value in [('mode', '040755'), ('uid', '0'), ('gid', '0')]:
            debug(fs, f'set_inode_field {quote(helper_work)} {field} {value}', write=True)
    migration = '/var/lib/orangepi-migration'
    if exists(fs, migration):
        if b'Type: directory' not in debug(fs, 'stat ' + quote(migration)):
            raise ValueError('Expected an actual installation evidence directory')
        for path, mode in children(fs, migration):
            if Path(path).name == 'installed-manifest.json':
                if mode & 0o170000 != 0o100000:
                    raise ValueError('Installed manifest must be a regular public file')
                manifest = json.loads(read(fs, path))
                if not isinstance(manifest.get('files'), list):
                    raise ValueError('Installed manifest is not a payload manifest')
                continue
            collect_private_material(path, mode)
            remove(fs, path)
    for home in ['/root', '/home/orangepi']:
        for name in ['.ssh', '.gnupg', '.cache', '.config', '.bash_history', '.zsh_history',
                     '.python_history', '.sqlite_history', '.lesshst', '.wget-hsts', '.Xauthority']:
            path = home + '/' + name
            collect_private_material(path)
            remove(fs, path)

    def remove_logs(directory):
        if not exists(fs, directory):
            return
        if b'Type: directory' not in debug(fs, 'stat ' + quote(directory)):
            raise ValueError('Expected an actual log directory')
        for path, mode in children(fs, directory):
            if mode & 0o170000 == 0o040000:
                remove_logs(path)
            else:
                collect_private_material(path, mode)
                remove(fs, path)
    remove_logs('/var/log')
    write(fs, '/etc/machine-id', b'', work)
    remove(fs, '/var/lib/dbus/machine-id')
    debug(fs, 'symlink /var/lib/dbus/machine-id /etc/machine-id', write=True)
    first_run_path = '/usr/lib/orangepi/orangepi-firstrun'
    first_run = read(fs, first_run_path).decode()
    original_sha = 'b6b061b1105a6ce58ac29a793f6f34ff8f69a5d513b57fe344b5a3af980d59ee'
    patched_sha = '7f721f15e81eb65e02bd9643feae8200ff0749e9d7a3af023f3dd6887cdcfd76'
    observed_sha = hashlib.sha256(first_run.encode()).hexdigest()
    if observed_sha == original_sha and first_run.count(SSH_BLOCK) == 1:
        first_run = first_run.replace(SSH_BLOCK, '\t# SSH keys are created before ssh by orangepi-image-identity.service.\n')
        write(fs, first_run_path, first_run.encode(), work, mode=0o755)
    elif observed_sha != patched_sha:
        raise RuntimeError('Official first-run script changed; SSH initialization was not patched')
    write(fs, '/etc/systemd/system/orangepi-image-identity.service', IDENTITY_UNIT, work)
    for unit in ['ssh.service', 'ssh@.service', 'ssh.socket']:
        write(fs, '/etc/systemd/system/' + unit + '.d/10-image-identity.conf', SSH_DEPENDENCY, work)
    if any(Path(path).name.startswith('ssh_host_') for path, _ in children(fs, '/etc/ssh')):
        raise RuntimeError('Host key files remain')
    for database in ['/etc/shadow', '/etc/gshadow']:
        if any(line.split(':')[1] != '!' for line in read(fs, database).decode().splitlines()):
            raise RuntimeError('An account credential remains unlocked')
    if read(fs, '/etc/machine-id'):
        raise RuntimeError('Machine ID was not cleared')
    report = {'original_official_firstrun_sha256': original_sha,
              'account_passwords_locked': True, 'group_passwords_locked': True,
              'saved_wifi_profiles': 0, 'ssh_host_keys_removed': True, 'machine_id_reset': True,
              'application_device_state_cleared': True, 'apt_download_cache_and_lists_cleared': True,
              'public_installed_manifest_preserved_when_present': True,
              'openvpn_example_keys_removed': True,
              'retained_public_test_vectors': ['GnuTLS library embedded self-test keys; not machine credentials']}
    return report, needles


def build(base, project, output):
    if digest(base) != OFFICIAL_SHA256:
        raise ValueError('Official image SHA-256 differs; refusing unknown base')
    output.mkdir(parents=True, exist_ok=False)
    work = output / 'work'
    work.mkdir(mode=0o700)
    fs = work / 'rootfs.ext4'
    extract(base, fs)
    check(fs, output / 'filesystem-before.txt')
    if b'BOARD=orangepizero3w\n' not in read(fs, '/etc/orangepi-release'):
        raise ValueError('Wrong target board')
    # Build the provisioning overlay directly from source, never private backups.
    stage = work / 'staging'
    (stage / 'etc').mkdir(parents=True)
    environment = {**os.environ, 'DESTDIR': str(stage)}
    result = subprocess.run(['sh', str(project / 'provision/install.sh')], env=environment,
                            capture_output=True)
    if result.returncode:
        raise RuntimeError('Offline provisioning staging failed')
    run(['python3', project / 'tools/install_rootfs.py', fs, stage, work / 'provision-check'])
    cleanup_report, needles = sanitize_filesystem(fs, work)
    report = {'name': NAME, 'kind': 'Wi-Fi provisioning foundation; Studio and robot runtime are not installed',
              'official_base_sha256': OFFICIAL_SHA256, 'board_boot_test': 'not-performed'}
    report.update(cleanup_report)
    report.update(rebuild_journal(fs))
    report.update(zero_free_space(fs))
    check(fs, output / 'filesystem-after.txt')
    image = output / (NAME + '.img')
    assemble(base, fs, image)
    report.update(scan_removed_credentials(image, needles))
    archive = output / (image.name + '.gz')
    with image.open('rb') as source, archive.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=6) as target:
            shutil.copyfileobj(source, target, 8 * 1024 * 1024)
    report['files'] = [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in [image, archive]]
    (output / 'image-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (output / 'IMG-SHA256SUMS').write_text(''.join(f"{item['sha256']}  {item['name']}\n" for item in report['files']))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    build(args.base_image.resolve(), args.project.resolve(), args.output_dir.resolve())
