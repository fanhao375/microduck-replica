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
                      exists, extract, read, remove, run, tool, write)

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


def rebuild_journal(fs):
    run([tool('tune2fs'), '-O', '^has_journal', fs])
    run([tool('tune2fs'), '-j', fs])
    blocks = list(map(int, debug(fs, 'blocks <8>').split()))
    block_size = int(re.search(rb'^Block size:\s*(\d+)', debug(fs, 'stats'), re.M)[1])
    # Retain only the freshly created JBD2 superblock, never journal records.
    with fs.open('r+b') as stream:
        stream.seek(blocks[0] * block_size)
        header = stream.read(1024)
        if struct.unpack('>I', header[:4])[0] != 0xc03b3998 or struct.unpack('>I', header[28:32])[0] != 0:
            raise RuntimeError('New journal is not clean')
        for block in blocks:
            stream.seek(block * block_size)
            stream.write(header + b'\0' * (block_size - 1024) if block == blocks[0] else b'\0' * block_size)
    return {'journal_recreated': True, 'journal_blocks_cleared': len(blocks)}


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
    needles = set()
    for path in KEY_PATHS:
        key = read(fs, path)
        needles.update(line for line in key.splitlines() if len(line) >= 32 and not line.startswith(b'-----'))
    # Public OpenVPN demo keys are unnecessary in a distributable device image.
    def collect_demo_key_material(directory):
        for path, mode in children(fs, directory):
            if mode & 0o170000 == 0o040000:
                collect_demo_key_material(path)
            elif mode & 0o170000 == 0o100000:
                content = read(fs, path)
                if b'PRIVATE KEY-----' in content:
                    for match in re.finditer(rb'-----BEGIN [^-]*PRIVATE KEY-----\s+(.*?)-----END [^-]*PRIVATE KEY-----', content, re.S):
                        needles.update(line for line in match[1].splitlines()
                                       if re.fullmatch(rb'[A-Za-z0-9+/=]{32,}', line))
    collect_demo_key_material('/usr/share/doc/openvpn/examples')
    remove(fs, '/usr/share/doc/openvpn/examples')
    shadow = read(fs, '/etc/shadow').decode()
    records = []
    for line in shadow.splitlines():
        fields = line.split(':')
        if fields[1].startswith('$'):
            needles.add(fields[1].encode())
        fields[1] = '!'
        records.append(':'.join(fields))
    write(fs, '/etc/shadow', ('\n'.join(records) + '\n').encode(), work, mode=0o640, gid=42)
    for path in ['/etc/shadow-', '/etc/gshadow-', '/etc/security/opasswd',
                 '/var/lib/systemd/random-seed', '/var/lib/urandom/random-seed',
                 '/boot/orangepi_first_run.txt', '/boot/orangepi_first_run.txt.old']:
        remove(fs, path)
    for directory in ['/etc/ssl/private', '/etc/NetworkManager/system-connections',
                      '/var/lib/NetworkManager', '/var/lib/orangepi-provision',
                      '/tmp', '/var/tmp']:
        for path, _ in children(fs, directory):
            remove(fs, path)
    for path, _ in children(fs, '/etc/ssh'):
        if Path(path).name.startswith('ssh_host_'):
            remove(fs, path)
    for home in ['/root', '/home/orangepi']:
        for name in ['.ssh', '.gnupg', '.cache', '.config', '.bash_history', '.zsh_history', '.wget-hsts', '.Xauthority']:
            remove(fs, home + '/' + name)
    def remove_logs(directory):
        for path, mode in children(fs, directory):
            if mode & 0o170000 == 0o040000:
                remove_logs(path)
            else:
                remove(fs, path)
    remove_logs('/var/log')
    write(fs, '/etc/machine-id', b'', work)
    remove(fs, '/var/lib/dbus/machine-id')
    debug(fs, 'symlink /var/lib/dbus/machine-id /etc/machine-id', write=True)
    first_run_path = '/usr/lib/orangepi/orangepi-firstrun'
    first_run = read(fs, first_run_path).decode()
    if first_run.count(SSH_BLOCK) != 1:
        raise RuntimeError('Official first-run SSH block changed')
    original_first_run_sha = hashlib.sha256(first_run.encode()).hexdigest()
    first_run = first_run.replace(SSH_BLOCK, '\t# SSH keys are created before ssh by orangepi-image-identity.service.\n')
    write(fs, first_run_path, first_run.encode(), work, mode=0o755)
    write(fs, '/etc/systemd/system/orangepi-image-identity.service', IDENTITY_UNIT, work)
    for unit in ['ssh.service', 'ssh@.service', 'ssh.socket']:
        write(fs, '/etc/systemd/system/' + unit + '.d/10-image-identity.conf', SSH_DEPENDENCY, work)
    for path in KEY_PATHS:
        if exists(fs, path):
            raise RuntimeError('Host key file remains')
    if any(line.split(':')[1] != '!' for line in read(fs, '/etc/shadow').decode().splitlines()):
        raise RuntimeError('An account remains unlocked')
    if read(fs, '/etc/machine-id'):
        raise RuntimeError('Machine ID was not cleared')
    report = {'name': NAME, 'kind': 'Wi-Fi provisioning foundation; Studio and robot runtime are not installed',
              'official_base_sha256': OFFICIAL_SHA256,
              'original_official_firstrun_sha256': original_first_run_sha,
              'account_passwords_locked': True, 'saved_wifi_profiles': 0,
              'ssh_host_keys_removed': True, 'machine_id_reset': True,
              'openvpn_example_keys_removed': True,
              'retained_public_test_vectors': ['GnuTLS library embedded self-test keys; not machine credentials'],
              'board_boot_test': 'not-performed'}
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
