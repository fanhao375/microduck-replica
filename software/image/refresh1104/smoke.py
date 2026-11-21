"""Exercise the shipped ARM Studio in a disposable overlay and a private network namespace."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request


def smoke(lower, work, version):
    upper, overlay_work, root = [work / name for name in ('upper', 'overlay-work', 'smoke-root')]
    for path in (upper, overlay_work, root):
        path.mkdir()
    subprocess.run(['mount', '--make-rprivate', '/'], check=True)
    subprocess.run(['mount', '-t', 'overlay', 'overlay', '-o',
                    f'lowerdir={lower},upperdir={upper},workdir={overlay_work}', root], check=True)
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    for name, mode in [('run', '0755'), ('tmp', '1777'), ('dev', '0755')]:
        subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=' + mode, 'tmpfs', root / name], check=True)
    for device in ('null', 'urandom'):
        target = root / 'dev' / device
        target.touch()
        subprocess.run(['mount', '--bind', '/dev/' + device, target], check=True)
    emu = shutil.which('qemu-aarch64-static') or shutil.which('qemu-aarch64')
    libs = subprocess.run(['ldd', emu], capture_output=True, text=True)
    for file in [emu, *re.findall(r'(/[^\s]+)\s+\(0x', libs.stdout)]:
        dest = root / file.lstrip('/')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, dest)
        dest.chmod(0o755)
    passwd = next(l.split(':') for l in (root / 'etc/passwd').read_text().splitlines() if l.startswith('radxa:'))
    uid, gid = map(int, passwd[2:4])
    for folder in ('run/microduck-studio', 'var/log/microduck-studio', 'var/lib/microduck-studio'):
        p = root / folder
        p.mkdir(parents=True, exist_ok=True)
        os.chown(p, uid, gid)
    # The verified preparation launcher execs this exact entry point for non-stock suites.
    # Invoke the ARM interpreter explicitly so the host needs no global binfmt registration.
    prefix = ['chroot', f'--userspec={uid}:{gid}', root, emu, '/usr/bin/python3', '-I',
              '/opt/microduck/software/current/studio/run.py']
    log = work / 'studio-arm.log'
    with log.open('w') as stream:
        process = subprocess.Popen([*prefix, '--listen', '127.0.0.1:8000', '--state-dir', '/var/lib/microduck-studio',
                                   '--log-dir', '/var/log/microduck-studio'], stdout=stream, stderr=stream)
    client = build_opener(ProxyHandler({}))

    def request(path, **headers):
        req = Request('http://127.0.0.1:8000' + path, headers=headers)
        try:
            with client.open(req, timeout=30) as response:
                return response.status, response.headers, response.read()
        except HTTPError as e:
            return e.code, e.headers, e.read()

    try:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError('Studio exited: ' + log.read_text()[-2000:])
            try:
                status, _, body = request('/healthz')
                if status == 200:
                    break
            except (URLError, TimeoutError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError('Studio startup timeout: ' + log.read_text()[-2000:])
        health = json.loads(body)
        if health.get('ok') is not True or health.get('suite_version') != version:
            raise ValueError(f'Unexpected Studio health response: {health}')
        web = lower / 'opt/microduck/software/current/studio/web'
        for path, file in [('/', 'index.html'), ('/app.js', 'app.js'), ('/m.html', 'm.html')]:
            print('ARM Studio HTTP cache check:', path, flush=True)
            code, headers, body = request(path, **{'If-Modified-Since': 'Thu, 01 Jan 1970 00:00:00 GMT',
                                                   'If-None-Match': '"former-version"'})
            digest = hashlib.sha256((web / file).read_bytes()).hexdigest()
            if code != 200 or hashlib.sha256(body).hexdigest() != digest or headers['ETag'] != f'"{digest}"':
                raise ValueError('Stale browser response for ' + path)
            if headers['Cache-Control'] != 'no-cache' or request(path, **{'If-Modified-Since':
                    'Thu, 01 Jan 1970 00:00:00 GMT'})[0] != 200:
                raise ValueError('Date-only revalidation failed for ' + path)
            if request(path, **{'If-None-Match': headers['ETag']})[0] != 304:
                raise ValueError('Content validator failed for ' + path)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == '__main__':
    smoke(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
