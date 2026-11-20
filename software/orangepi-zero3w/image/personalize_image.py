#!/usr/bin/env python3
"""Create a private image with the operator's chosen orangepi password.

The input Release image is never modified. Passwords are read from the terminal,
not arguments or environment variables; hashes are never printed.
"""
import argparse
import getpass
import os
import re
import shutil
import tempfile
import time
import warnings
from pathlib import Path

from image_fs import assemble, check, debug, digest, exists, extract, read, run, write


def openssl():
    for path in ['/opt/homebrew/opt/openssl@3/bin/openssl',
                 '/usr/local/opt/openssl@3/bin/openssl']:
        if Path(path).is_file():
            return path
    path = shutil.which('openssl')
    if not path:
        raise RuntimeError('Install OpenSSL 3 (for passwd -6)')
    return path


def personalize(source, output, expected_sha256):
    if source == output or output.exists():
        raise ValueError('Choose a new output path; existing files are never overwritten')
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256) or digest(source) != expected_sha256:
        raise ValueError('Input SHA-256 verification failed')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('New orangepi password (at least 12 characters): ')
        confirmation = getpass.getpass('Repeat password: ')
    if password != confirmation or len(password) < 12 or any(c in password for c in '\r\n\0'):
        raise ValueError('Passwords must match, have at least 12 characters, and contain no newlines or NUL')
    hashed = run([openssl(), 'passwd', '-6', '-stdin'], data=password.encode() + b'\n').strip()
    del password, confirmation
    if not re.fullmatch(rb'\$6\$[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}', hashed):
        raise RuntimeError('OpenSSL did not return a SHA-512 crypt hash')
    with tempfile.TemporaryDirectory(prefix='orangepi-personalize-') as temporary:
        work = Path(temporary)
        fs = work / 'rootfs.ext4'
        extract(source, fs)
        check(fs, work / 'check-before.txt')
        if (b'BOARD=orangepizero3w\n' not in read(fs, '/etc/orangepi-release')
                or not exists(fs, '/etc/systemd/system/orangepi-image-identity.service')):
            raise ValueError('Expected the sanitized Orange Pi provisioning image')
        records = []
        changed = 0
        for line in read(fs, '/etc/shadow').decode().splitlines():
            fields = line.split(':')
            if fields[1] != '!':
                raise ValueError('Input image is already personalized or contains a password')
            if fields[0] == 'orangepi':
                fields[1] = hashed.decode()
                fields[2] = str(int(time.time() // 86400))
                changed += 1
            records.append(':'.join(fields))
        if changed != 1:
            raise ValueError('Expected exactly one orangepi account')
        write(fs, '/etc/shadow', ('\n'.join(records) + '\n').encode(), work, mode=0o640, gid=42)
        stat = debug(fs, 'stat /etc/shadow')
        if not re.search(rb'Mode:\s+0640', stat) or not re.search(rb'User:\s+0\s+Group:\s+42', stat):
            raise RuntimeError('Shadow permissions or ownership did not verify')
        if any(exists(fs, f'/etc/ssh/ssh_host_{kind}_key') for kind in ['rsa', 'ecdsa', 'ed25519']):
            raise RuntimeError('Input image has pre-existing host keys')
        check(fs, work / 'check-after.txt')
        result = work / 'personalized.img'
        assemble(source, fs, result)
        result.chmod(0o600)
        # Exclusive creation also protects against a race after the early check.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as target, result.open('rb') as source_stream:
                shutil.copyfileobj(source_stream, target, 8 * 1024 * 1024)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    print(f'Created private personalized image: {output}')
    print('Flash this file. Login as orangepi with your chosen password; root remains locked.')
    print('Keep this personalized image private. It contains your password hash.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--sha256', required=True, help='Verified uncompressed Release image SHA-256')
    args = parser.parse_args()
    personalize(args.source.resolve(), args.output.resolve(), args.sha256.lower())
