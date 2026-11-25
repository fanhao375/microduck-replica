#!/usr/bin/env python3
"""Install only duck-echo; never reload or restart the robot motion daemons."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

BASE = Path(__file__).resolve().parent
STATE = Path('/var/lib/duck-echo')
SOURCE_FILE = Path('/var/lib/microduck-studio/microphone-source.json')
MICROPHONE_SOURCES = ('onboard', 'external', 'both')
ONBOARD_CONTROLS = ('Right PGA Mixer Mic3R Switch', 'Right PGA Mixer Mic2R Switch')
EXTERNAL_CONTROL = 'Right PGA Mixer Line1L Switch'
DESTINATION = Path('/opt/duck-echo')
UNIT = Path('/etc/systemd/system/duck-echo.service')
DEFAULTS = Path('/etc/default/duck-echo')
RUNTIME_FILES = ('duck_echo.py', 'install.py', 'duck-echo.service', 'README.md',
                 'voice_commands.py', 'speech_control.py', 'install_speech.py', 'pad_mapping.py',
                 'continuous_audio.py', 'music_feel.py', 'music_interaction.py', 'audio_control.py')
CAPTURE_SETTINGS = {
    'Right PGA Mixer Mic3R Switch': 'on',
    'Left PGA Mixer Mic3R Switch': 'off',
    'Left PGA Mixer Mic3L Switch': 'off',
    'Right PGA Mixer Mic3L Switch': 'off',
    'Right PGA Mixer Line1R Switch': 'off',
    'Right PGA Mixer Line1L Switch': 'off',
    'Left PGA Mixer Line1R Switch': 'off',
    'Left PGA Mixer Line1L Switch': 'off',
    'Right PGA Mixer Line2R Switch': 'off',
    'PGA Capture Switch': 'on,on',
    'PGA Capture Volume': '60,60',
}


def run(arguments, check=True):
    return subprocess.run(arguments, check=check, capture_output=True, text=True, timeout=15)


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def mixer(card, action, name, value=None):
    arguments = ['amixer', '-c', card, action, 'name=' + name]
    if value is not None:
        arguments.append(value)
    output = run(arguments).stdout
    match = re.search(r'^\s*: values=(.+)$', output, re.MULTILINE)
    if not match:
        raise RuntimeError('no mixer readback for ' + name)
    return match[1].strip()


def mixer_controls(card):
    listing = run(['amixer', '-c', card, 'controls']).stdout
    return set(re.findall(r"name='([^']+)'", listing))


def onboard_control(names):
    return next((name for name in ONBOARD_CONTROLS if name in names), None)


class MicrophoneRoutes:
    """Select the analog inputs to the right ADC. Studio owns the preference."""
    def __init__(self, card, path=SOURCE_FILE):
        self.card = card
        self.path = Path(path)
        self.source = None

    @classmethod
    def for_capture(cls, device, channel):
        match = re.fullmatch(r'(?:plug)?hw:(?:CARD=)?([A-Za-z0-9_-]+)(?:,(?:DEV=)?\d+)?', device)
        if channel == 1 and match:
            return cls(match[1])
        return None

    def restore(self):
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(257)
        except FileNotFoundError:
            source = 'onboard'
        else:
            if len(raw) > 256:
                raise ValueError('invalid saved microphone source')
            preference = json.loads(raw)
            if not isinstance(preference, dict) or set(preference) != {'source'}:
                raise ValueError('invalid saved microphone source')
            source = preference['source']
        self.select(source)

    def select(self, source):
        if source not in MICROPHONE_SOURCES:
            raise ValueError('invalid microphone source')
        self.source = None
        names = mixer_controls(self.card)
        onboard = onboard_control(names)
        if onboard is None:
            raise RuntimeError('onboard AIC3x microphone control unavailable')
        desired = {onboard: 'off' if source == 'external' else 'on'}
        if EXTERNAL_CONTROL in names:
            desired[EXTERNAL_CONTROL] = 'off' if source == 'onboard' else 'on'
        current = {n: mixer(self.card, 'cget', n) for n in desired}
        observed = (current[onboard], current.get(EXTERNAL_CONTROL, 'off'))
        self.source = {('on', 'off'): 'onboard', ('off', 'on'): 'external',
                       ('on', 'on'): 'both'}.get(observed)
        if source != 'onboard' and EXTERNAL_CONTROL not in names:
            raise RuntimeError('external J2 Line1L control unavailable')
        changed = []
        try:
            for name, value in desired.items():
                if current[name] == value:
                    continue
                changed.append(name)
                if mixer(self.card, 'cset', name, value) != value:
                    raise RuntimeError('microphone switch readback mismatch: ' + name)
            if changed and any(mixer(self.card, 'cget', n) != v for n, v in desired.items()):
                raise RuntimeError('microphone route verification failed')
        except Exception:
            failed = []
            for name in reversed(changed):
                try:
                    if mixer(self.card, 'cset', name, current[name]) != current[name]:
                        failed.append(name)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    failed.append(name)
            if failed:
                self.source = None
                raise RuntimeError('microphone route rollback failed: ' + ', '.join(failed))
            raise
        self.source = source


def prepare_microphone(card, backup):
    # Read every control BEFORE changing any; abort cleanly for a different codec.
    names = mixer_controls(card)
    microphone = onboard_control(names)
    if microphone is None or not {'PGA Capture Switch', 'PGA Capture Volume'} <= names:
        raise RuntimeError('known AIC3x right microphone controls not found; mixer left unchanged')
    settings = {name: 'off' for name in sorted(names)
                if re.fullmatch(r'(Left|Right) PGA Mixer \w+ Switch', name)}
    settings.update({microphone: 'on', 'PGA Capture Switch': 'on,on', 'PGA Capture Volume': '60,60'})
    current = {name: mixer(card, 'cget', name) for name in settings}
    if not backup.exists():
        atomic_json(backup, {'card': card, 'values': current})
    else:
        saved = json.loads(backup.read_text())
        if saved['card'] != card:
            raise RuntimeError('existing microphone backup belongs to another card')
    changed = []
    try:
        for name, value in settings.items():
            changed.append(name)
            actual = mixer(card, 'cset', name, value)
            if actual != value:
                raise RuntimeError('unexpected mixer readback: ' + name + '=' + actual)
    except BaseException:
        for name in reversed(changed):
            mixer(card, 'cset', name, current[name])
        raise
    return current


def restore_microphone(backup):
    if backup.exists():
        saved = json.loads(backup.read_text())
        for name, value in saved['values'].items():
            if mixer(saved['card'], 'cset', name, value) != value:
                raise RuntimeError('microphone restore readback mismatch: ' + name)
        # Keep evidence but retire the active baseline. A future installation
        # must restore the settings from that installation, not the first ever one.
        os.replace(backup, backup.with_suffix('.restored.json'))


def motion_snapshot():
    return {name: run([
        'systemctl', 'show', name, '--property=ActiveState,MainPID,ExecMainStartTimestampMonotonic',
    ], check=False).stdout for name in ('robotd', 'padd')}


class InstallationRollback:
    """Restore this attempt, keeping the uninstall baseline for an active upgrade."""
    def __init__(self, card, backup):
        self.card, self.backup = card, backup
        self.mixer_values = {}

    def __enter__(self):
        self.active = run(['systemctl', 'is-active', '--quiet', 'duck-echo.service'], check=False).returncode == 0
        self.enabled = run(['systemctl', 'is-enabled', '--quiet', 'duck-echo.service'], check=False).returncode == 0
        paths = [DESTINATION / name for name in RUNTIME_FILES]
        paths += [UNIT, DEFAULTS, self.backup, STATE / 'motion-before.json']
        self.files = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
                      for path in paths}
        return self

    def __exit__(self, kind, error, traceback):
        if kind is None:
            return False
        failures = []

        def attempt(operation):
            try:
                operation()
            except Exception as failure:
                failures.append(str(failure))

        attempt(lambda: run(['systemctl', 'stop', 'duck-echo.service'], check=False))
        for path, saved in self.files.items():
            def restore_file(path=path, saved=saved):
                if saved is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(saved[0])
                    path.chmod(saved[1])
            attempt(restore_file)
        for name, value in self.mixer_values.items():
            def restore_control(name=name, value=value):
                if mixer(self.card, 'cset', name, value) != value:
                    raise RuntimeError('rollback mixer readback mismatch: ' + name)
            attempt(restore_control)
        attempt(lambda: run(['systemctl', 'daemon-reload']))
        attempt(lambda: run(['systemctl', 'enable' if self.enabled else 'disable', 'duck-echo.service'], check=False))
        if self.active:
            attempt(lambda: run(['systemctl', 'start', 'duck-echo.service']))
        if failures:
            print('INSTALL ROLLBACK INCOMPLETE: ' + '; '.join(failures), file=sys.stderr)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='install',
                        choices=['install', 'prepare-mic', 'restore-mic', 'uninstall'])
    parser.add_argument('--user', default='radxa')
    parser.add_argument('--card', default='aic3104')
    args = parser.parse_args(argv)
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('run on the duck with sudo python3 install.py')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', args.user):
        parser.error('invalid service user')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.card):
        parser.error('invalid ALSA card')
    backup = STATE / 'mixer-before.json'
    STATE.mkdir(parents=True, exist_ok=True)
    if args.action == 'restore-mic':
        restore_microphone(backup)
        return 0
    if args.action == 'prepare-mic':
        prepare_microphone(args.card, backup)
        print('Mic3R capture prepared; original settings saved to', backup)
        return 0
    before = motion_snapshot()
    if args.action == 'uninstall':
        run(['systemctl', 'disable', '--now', 'duck-echo.service'], check=False)
        restore_microphone(backup)
        UNIT.unlink(missing_ok=True)
        run(['systemctl', 'daemon-reload'])
        print('Echo service removed; capture mixer restored. Files retained in', DESTINATION)
    else:
        run(['id', args.user])
        for group in ('audio', 'input', 'robot'):
            run(['getent', 'group', group])
        for executable in ('arecord', 'aplay', 'amixer', 'systemctl'):
            if not shutil.which(executable):
                raise RuntimeError('missing required executable: ' + executable)
        with InstallationRollback(args.card, backup) as attempt:
            # Only our own service is stopped during an upgrade.
            run(['systemctl', 'stop', 'duck-echo.service'], check=False)
            attempt.mixer_values = prepare_microphone(args.card, backup)
            DESTINATION.mkdir(parents=True, exist_ok=True)
            for name in RUNTIME_FILES:
                source, destination = BASE / name, DESTINATION / name
                if source.resolve() != destination.resolve():
                    shutil.copyfile(source, destination)
                destination.chmod(0o644)
            unit = (BASE / 'duck-echo.service').read_text().replace('User=radxa', 'User=' + args.user)
            UNIT.write_text(unit)
            UNIT.chmod(0o644)
            if not DEFAULTS.exists():
                DEFAULTS.write_text(
                    '# R3 microphone listening. pitch=1.0 means original pitch and speed.\n'
                    f'ECHO_CAPTURE=plughw:CARD={args.card},DEV=0\n'
                    f'ECHO_PLAYBACK=plughw:CARD={args.card},DEV=0\n'
                    'ECHO_CHANNEL=1\nECHO_PITCH=1.25\nECHO_VOICE_MODE=off\nECHO_TRIGGER_MODE=toggle\nECHO_REPLY_MODE=duck\n')
            atomic_json(STATE / 'motion-before.json', before)
            run(['systemctl', 'daemon-reload'])
            run(['systemctl', 'enable', '--now', 'duck-echo.service'])
            run(['systemctl', 'is-active', 'duck-echo.service'])
        print('Echo service started. Press R3 to enable listening; press again to disable.')
        print('Set ECHO_TRIGGER_MODE=hold in /etc/default/duck-echo for hold-to-talk.')
        print('Microphone and speaker still require physical audio verification.')
    after = motion_snapshot()
    if before != after:
        print('NOTICE: robotd/padd state changed during this operation; inspect their logs.', file=sys.stderr)
    else:
        print('robotd and padd service process/state snapshots are unchanged.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('duck-echo setup failed:', error, file=sys.stderr)
        sys.exit(1)
