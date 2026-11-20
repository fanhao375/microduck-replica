"""Install an independent voice Studio for the current Linux user.

The signed system installation is left to its updater. Code/dependencies are
frozen together; login and calibration state are imported only on first install.
Enabling user services alone does not grant permission to run before login:
an administrator must enable linger separately.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

SERVICES = ('microduck-voice.service', 'microduck-voice-https.service')


def install(source, state_source, tls_source, home, names=()):
    source, state_source, tls_source, home = (
        Path(path).resolve() for path in (source, state_source, tls_source, home))
    # The hostname and hostname.local are already accepted by Studio. Explicit
    # IP names are optional conveniences; do not accept arbitrary Host patterns.
    names = tuple(str(ipaddress.ip_address(name)) for name in names)
    required = [source / name for name in ('run.py', 'studio', 'web', 'deps')]
    required += [source / 'deploy' / name for name in (*SERVICES, 'https_proxy.py')]
    required += [state_source / 'password.json', tls_source / 'server.crt', tls_source / 'server.key']
    if any(not path.exists() for path in required):
        raise ValueError('Source, existing administrator password, or TLS files are missing')

    root = home / '.local/share/microduck-voice'
    state = home / '.local/state/microduck-voice'
    units = home / '.config/systemd/user'
    for directory in (root / 'releases', units):
        directory.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    release = Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%d-%H%M%S-'), dir=root / 'releases'))
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '._*', '.DS_Store')
    try:
        shutil.copy2(source / 'run.py', release / 'run.py')
        for name in ('studio', 'web', 'deps'):
            shutil.copytree(source / name, release / name, ignore=ignore)
        (release / 'deploy').mkdir()
        shutil.copy2(source / 'deploy/https_proxy.py', release / 'deploy/https_proxy.py')

        # Atomic first import and no subsequent overwrite: reinstalling must not
        # reset a password, resurrect old sessions, or replace newer calibration.
        if not state.exists():
            state.parent.mkdir(parents=True, exist_ok=True)
            staged = Path(tempfile.mkdtemp(prefix='.voice-state-', dir=state.parent))
            try:
                shutil.copytree(state_source, staged, dirs_exist_ok=True)
                staged.chmod(0o700)
                os.replace(staged, state)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        state.chmod(0o700)
        for name in ('password.json', 'sessions.json'):
            path = state / name
            if path.is_file():
                path.chmod(0o600)
        tls = root / 'tls'
        tls.mkdir(mode=0o700, exist_ok=True)
        tls.chmod(0o700)
        for name in ('server.crt', 'server.key'):
            path = tls / name
            if not path.exists():
                shutil.copy2(tls_source / name, path)
            path.chmod(0o600)

        for name in SERVICES:
            text = (source / 'deploy' / name).read_text()
            if name == SERVICES[0] and names:
                text = text.replace('--log-dir %h/.local/state/microduck-voice/logs',
                                    '--log-dir %h/.local/state/microduck-voice/logs' +
                                    ''.join(' --name ' + value for value in names))
            temporary = units / (name + '.tmp')
            temporary.write_text(text)
            os.replace(temporary, units / name)
        current = root / 'current'
        if current.is_symlink():
            previous = root / 'previous.new'
            previous.symlink_to(os.readlink(current))
            os.replace(previous, root / 'previous')
        link = root / 'current.new'
        link.symlink_to(release.relative_to(root))
        os.replace(link, current)
    except BaseException:
        if not (root / 'current').is_symlink() or (root / 'current').resolve() != release:
            shutil.rmtree(release)
        raise
    return {'release': str(release), 'state': str(state), 'units': str(units)}


def main():
    if os.geteuid() == 0:
        raise SystemExit('Run as the Studio user, without sudo')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--state-source', type=Path, default=Path('/var/lib/microduck-studio'))
    parser.add_argument('--tls-source', type=Path, required=True)
    parser.add_argument('--name', action='append', default=[])
    args = parser.parse_args()
    print(json.dumps(install(args.source, args.state_source, args.tls_source, Path.home(), args.name)))
    print('Next: systemctl --user daemon-reload && systemctl --user enable --now ' + ' '.join(SERVICES))
    print('Boot persistence requires an administrator: sudo loginctl enable-linger ' + str(os.getuid()))


if __name__ == '__main__':
    main()
