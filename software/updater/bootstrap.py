"""One-time factory/legacy bootstrap using the SAME signed package and Rust engine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import stat

from package import unpack_outer
from service import DEFAULT_CONFIG

HERE=Path(__file__).resolve().parent
STABLE='/usr/local/lib/microduck-updater'

def profile(root='/'):
    root=Path(root)
    def at(path): return (root/path.lstrip('/')).as_posix()
    q=lambda value:json.dumps(value)
    live=root.resolve()==Path('/').resolve()
    return f'''trusted_keys_dir = {q(at('/etc/microduck-updater/trusted_keys'))}
hw_rev = 1
state_dir = {q(at('/var/lib/microduck-updater/engine'))}
auto_apply = "off"
allow_dev_keys = false
allow_users = []
max_uncompressed_bytes = 268435456
max_archive_entries = 10000
[component.software]
install_dir = {q(at('/opt/microduck/software'))}
keep_previous = 2
required_files = ["studio/run.py", "studio/web/app.js", "duck-echo/duck_echo.py", "studio/deploy/microduck-helper", "suite-version.json"]
max_artifact_bytes = 134217728
[component.software.source]
type = "local_dir"
path = {q(at('/var/lib/microduck-updater/catalog'))}
[component.software.on_apply]
action = "{'restart' if live else 'none'}"
''' + ('units = ["microduck-studio", "duck-echo"]\n' if live else '') + (f'''[component.software.health]
probe = "command"
program = "/usr/bin/python3"
args = ["{STABLE}/health.py"]
timeout = "90s"
''' if live else '[component.software.health]\nprobe = "none"\n')

STUDIO='''[Unit]
Description=Microduck Studio
After=network.target microduck-update-control.service
[Service]
User=radxa
Group=radxa
RuntimeDirectory=microduck-studio
RuntimeDirectoryMode=0700
StateDirectory=microduck-studio
StateDirectoryMode=0700
LogsDirectory=microduck-studio
LogsDirectoryMode=0750
WorkingDirectory=/opt/microduck/software/current/studio
ExecStart=/usr/bin/python3 -I run.py --listen 0.0.0.0:8000 --state-dir /var/lib/microduck-studio --log-dir /var/log/microduck-studio
Restart=on-failure
RestartSec=3
Nice=10
CPUWeight=20
CPUQuota=60%
MemoryMax=300M
[Install]
WantedBy=multi-user.target
'''
VOICE='''[Unit]
Description=Microduck local R3 voice
Requires=microduck-voice-prepare.service
After=sound.target microduck-voice-prepare.service microduck-update-control.service
[Service]
User=radxa
SupplementaryGroups=audio input robot
Environment=PYTHONUNBUFFERED=1
Environment=ECHO_CAPTURE=plughw:CARD=aic3104,DEV=0
Environment=ECHO_PLAYBACK=plughw:CARD=aic3104,DEV=0
Environment=ECHO_CHANNEL=1
Environment=ECHO_PITCH=1.25
Environment=ECHO_VOICE_MODE=actions
Environment=ECHO_TRIGGER_MODE=toggle
Environment=ECHO_REPLY_MODE=duck
Environment=OPENBLAS_NUM_THREADS=1
Environment=OMP_NUM_THREADS=1
EnvironmentFile=-/etc/default/duck-echo
RuntimeDirectory=duck-echo
RuntimeDirectoryMode=0700
ExecStart=/usr/bin/python3 /opt/microduck/software/current/duck-echo/duck_echo.py daemon --capture-device ${ECHO_CAPTURE} --playback-device ${ECHO_PLAYBACK} --channel ${ECHO_CHANNEL} --pitch ${ECHO_PITCH} --voice-mode ${ECHO_VOICE_MODE} --trigger-mode ${ECHO_TRIGGER_MODE} --reply-mode ${ECHO_REPLY_MODE} --speech-deps /opt/microduck/resources/asr-deps --voice-model /opt/microduck/resources/models/vosk-model-small-cn-0.22 --control-socket /run/duck-echo/control.sock
Restart=on-failure
RestartSec=3
TimeoutStopSec=5
KillMode=control-group
Nice=10
MemoryMax=640M
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
RestrictAddressFamilies=AF_UNIX
[Install]
WantedBy=multi-user.target
'''
MIC='''[Unit]
Description=Prepare Microduck Mic3R capture route
After=sound.target aic3104-init.service alsa-restore.service
Before=duck-echo.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/microduck/software/current/duck-echo/install.py prepare-mic
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
'''
ENGINE=f'''[Unit]
Description=Microduck signed software transaction engine
[Service]
User=root
RuntimeDirectory=microduck-updater
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
# 新装的版本目录要让 radxa 跑的中控和语音服务读得到：0077 会建成只有 root 能进，装完必然起不来、整体退回
UMask=0022
Environment=PATH=/usr/local/lib/microduck-updater/engine-bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart={STABLE}/updaterd --config /etc/microduck-updater/updater.toml --socket /run/microduck-updater/engine.sock --token /var/lib/microduck-updater/unused-account.json
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/microduck/software /var/lib/microduck-updater /run/microduck-updater
[Install]
WantedBy=multi-user.target
'''
CONTROL=f'''[Unit]
Description=Microduck bounded software update coordinator
After=microduck-updater-engine.service
Requires=microduck-updater-engine.service
[Service]
User=root
RuntimeDirectory=microduck-updater
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
UMask=0077
ExecStartPre=/usr/bin/touch /run/microduck-updater/motion.lock
ExecStartPre=/usr/bin/chmod 0644 /run/microduck-updater/motion.lock
ExecStart=/usr/bin/python3 -I {STABLE}/run_control.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
'''
UNITS={'microduck-studio.service':STUDIO,'duck-echo.service':VOICE,'microduck-voice-prepare.service':MIC,
       'microduck-updater-engine.service':ENGINE,'microduck-update-control.service':CONTROL}
STUDIO_OVERRIDE='[Service]\nExecStart=\n'+next(line for line in STUDIO.splitlines() if line.startswith('ExecStart='))+'\n'

def copy_regular_tree(source,destination,source_uid,target_uid=None,target_gid=None):
    """Snapshot untrusted user state with pinned no-follow descriptors only.

    Destination must be a new directory beneath the root-private backup. Never
    chown or traverse an existing user tree, symlink, hardlink, or special file.
    """
    source,destination=Path(source),Path(destination)
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    source_fd=os.open(source,flags);destination_fd=None;created=False
    count=0;total=0
    try:
        if os.fstat(source_fd).st_uid!=source_uid:raise ValueError('unexpected legacy directory owner')
        destination.mkdir(mode=0o700);created=True;destination_fd=os.open(destination,flags)
        def copy_directory(src,dst):
            nonlocal count,total
            for name in os.listdir(src):
                count+=1
                if count>20000:raise ValueError('legacy state has too many entries')
                before=os.stat(name,dir_fd=src,follow_symlinks=False)
                if stat.S_ISDIR(before.st_mode):
                    child=os.open(name,flags,dir_fd=src)
                    try:
                        if os.fstat(child).st_uid!=source_uid:raise ValueError('unexpected legacy directory owner')
                        os.mkdir(name,0o700,dir_fd=dst);out=os.open(name,flags,dir_fd=dst)
                        try:
                            copy_directory(child,out)
                            if target_uid is not None:os.fchown(out,target_uid,target_gid)
                        finally:os.close(out)
                    finally:os.close(child)
                elif stat.S_ISREG(before.st_mode):
                    incoming=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=src)
                    try:
                        info=os.fstat(incoming)
                        if not stat.S_ISREG(info.st_mode) or info.st_uid!=source_uid or info.st_nlink!=1:
                            raise ValueError('unsafe legacy state file')
                        total+=info.st_size
                        if info.st_size>64*1024*1024 or total>512*1024*1024:raise ValueError('legacy state exceeds migration bounds')
                        outgoing=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dst)
                        try:
                            remaining=info.st_size
                            while remaining:
                                block=os.read(incoming,min(1024*1024,remaining))
                                if not block:raise ValueError('legacy file changed during snapshot')
                                view=memoryview(block)
                                while view:view=view[os.write(outgoing,view):]
                                remaining-=len(block)
                            if os.read(incoming,1):raise ValueError('legacy file changed during snapshot')
                            if target_uid is not None:os.fchown(outgoing,target_uid,target_gid)
                            os.fsync(outgoing)
                        finally:os.close(outgoing)
                    finally:os.close(incoming)
                else:raise ValueError('legacy state contains a link or special file')
            os.fsync(dst)
        copy_directory(source_fd,destination_fd)
        if target_uid is not None:os.fchown(destination_fd,target_uid,target_gid)
    except Exception:
        if created:shutil.rmtree(destination)
        raise
    finally:
        os.close(source_fd)
        if destination_fd is not None:os.close(destination_fd)

def migrate_state(source,destination,backup,uid,gid):
    destination=Path(destination);backup=Path(backup)
    if destination.is_symlink():raise ValueError('state directory cannot be a symbolic link')
    if any(destination.iterdir()):return False
    snapshot=backup/'legacy-state';staged=backup/'state-staging'
    copy_regular_tree(source,snapshot,uid)
    copy_regular_tree(snapshot,staged,os.getuid(),uid,gid)
    # Parent /var/lib is root-owned: if a user filled the existing destination
    # during our snapshot, replacing the nonempty directory fails safely.
    os.replace(staged,destination)
    return True

def app_directories(root):
    identity=None
    if os.name=='posix':
        try:
            for line in (Path(root)/'etc/passwd').read_text().splitlines():
                fields=line.split(':')
                if fields[0]=='radxa':identity=(int(fields[2]),int(fields[3]));break
        except FileNotFoundError:pass
    for relative,mode in (('var/lib/microduck-studio',0o700),('var/log/microduck-studio',0o750)):
        path=Path(root)/relative;path.mkdir(parents=True,exist_ok=True)
        if os.name=='posix':
            descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                # Only the directory itself; existing user files retain ownership.
                if identity is not None:os.fchown(descriptor,*identity)
                os.fchmod(descriptor,mode)
            finally:os.close(descriptor)

def write(root,path,text,mode=0o644):
    target=Path(root)/path.lstrip('/'); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(text,encoding='utf-8',newline='\n'); target.chmod(mode)

def install_stable(root,engine,public_key):
    root=Path(root)
    for directory in ('opt/microduck/software','var/lib/microduck-updater/catalog','var/lib/microduck-updater/control',
                      'var/lib/microduck-studio','etc/microduck-updater/trusted_keys',STABLE.lstrip('/')):
        (root/directory).mkdir(parents=True,exist_ok=True)
    (root/'var/lib/microduck-updater').chmod(0o700)
    for filename in ('package.py','control.py','service.py','health.py','run_control.py','engine_systemctl.py'):
        shutil.copyfile(HERE/filename,root/STABLE.lstrip('/')/filename)
        (root/STABLE.lstrip('/')/filename).chmod(0o644)
    shims=root/STABLE.lstrip('/')/'engine-bin'; shims.mkdir(exist_ok=True)
    for name in ('systemctl','systemd-run'):
        shutil.copyfile(HERE/'engine_systemctl.py',shims/name); (shims/name).chmod(0o755)
    shutil.copyfile(engine,root/STABLE.lstrip('/')/'updaterd'); (root/STABLE.lstrip('/')/'updaterd').chmod(0o755)
    shutil.copyfile(public_key,root/'etc/microduck-updater/trusted_keys/release.pub')
    write(root,'/etc/microduck-updater/updater.toml',profile('/'),0o600)
    write(root,'/etc/microduck-updater/control.json',json.dumps(DEFAULT_CONFIG,indent=2)+'\n',0o600)
    for name,content in UNITS.items(): write(root,'/etc/systemd/system/'+name,content)
    write(root,'/etc/systemd/system/microduck-studio.service.d/90-software-update.conf',STUDIO_OVERRIDE)
    app_directories(root)
    write(root,'/usr/local/sbin/microduck-helper','#!/bin/sh\nset -eu\nexec /opt/microduck/software/current/studio/deploy/microduck-helper "$@"\n',0o755)
    (root/'etc/sudoers.d').mkdir(parents=True,exist_ok=True)
    sudoers=root/'etc/sudoers.d/microduck-studio'
    if sudoers.exists(): sudoers.chmod(0o644)
    shutil.copyfile(HERE.parent/'studio/deploy/sudoers-microduck-studio',sudoers)
    sudoers.chmod(0o440)

def verify_tree(tree):
    tree=Path(tree).resolve(); metadata=json.loads((tree/'suite-version.json').read_bytes())
    for name,want in metadata['files'].items():
        file=tree/name
        if not file.resolve().is_relative_to(tree) or file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest()!=want:
            raise ValueError('installed runtime file hash mismatch: '+name)
    return metadata

def bootstrap(root,package,public_key,engine,engine_command=None,offline=False):
    root=Path(root).resolve()
    if not root.is_dir(): raise ValueError('root must be an existing directory')
    live=root==Path('/')
    if live and offline: raise ValueError('--offline requires an isolated image root')
    if not live and not offline: raise ValueError('a non-live root requires --offline')
    if (root/'opt/microduck/software/current').exists(): raise ValueError('already bootstrapped; use authenticated update control instead')
    state=root/'var/lib/microduck-updater'; state.mkdir(parents=True,exist_ok=True,mode=0o700); state.chmod(0o700)
    backup=state/('bootstrap-backup-'+str(time.time_ns())); backup.mkdir(mode=0o700)
    replaced=['etc/systemd/system/'+name for name in UNITS]+[
        'etc/systemd/system/multi-user.target.wants/'+name for name in UNITS]+[
        'etc/microduck-updater','usr/local/sbin/microduck-helper','etc/sudoers.d/microduck-studio',STABLE.lstrip('/'),'opt/microduck/software',
        'etc/systemd/system/microduck-studio.service.d/90-software-update.conf']
    original=set()
    for name in replaced:
        source=root/name
        if source.exists() or source.is_symlink():
            original.add(name); target=backup/name; target.parent.mkdir(parents=True,exist_ok=True)
            if source.is_dir(): shutil.copytree(source,target,symlinks=True)
            else: shutil.copy2(source,target,follow_symlinks=False)
    stopped=False;gate_descriptor=None
    live_marker=root/'run/microduck-updater/installing'
    try:
        if live:
            for legacy in ('home/radxa/studio-dev','opt/duck-echo'):
                # Legacy code remains untouched in place. Snapshot only the
                # fixed user-owned source via no-follow descriptors as well.
                if (root/legacy).is_dir():
                    import pwd
                    owner=pwd.getpwnam('radxa').pw_uid if legacy.startswith('home/') else os.stat(root/legacy).st_uid
                    target=backup/'legacy-runtime'/legacy;target.parent.mkdir(parents=True,exist_ok=True)
                    copy_regular_tree(root/legacy,target,owner)
        for unit_name in UNITS:
            for dropin in (root/'etc/systemd/system'/f'{unit_name}.d').glob('*.conf'):
                for line in dropin.read_text(encoding='utf-8').splitlines():
                    if line.startswith('WorkingDirectory=') and line!='WorkingDirectory=/opt/microduck/software/current/studio':
                        raise ValueError('custom service WorkingDirectory requires migration: '+str(dropin))
                    if line.startswith('ExecStart=') and line!='ExecStart=':
                        known=(unit_name=='microduck-studio.service' and dropin.name=='10-image.conf'
                            and line=='ExecStart=/usr/bin/python3 -I run.py --listen 0.0.0.0:8000')
                        known=known or (unit_name=='microduck-studio.service' and dropin.name=='90-software-update.conf'
                            and dropin.read_text()==STUDIO_OVERRIDE)
                        if not known:raise ValueError('custom service ExecStart requires migration: '+str(dropin))
        # Write stable operator-provided tools, never run anything from unverified
        # package members. The original engine verifies before the first swap.
        install_stable(root,engine,public_key)
        cache=state/'bootstrap-cache'
        if cache.exists(): shutil.rmtree(cache)
        with open(package,'rb') as stream: manifest=unpack_outer(stream,cache)
        temporary_profile=backup/'build-profile.toml'; temporary_profile.write_text(profile(root),encoding='utf-8')
        command=[*(engine_command or []),str(root/STABLE.lstrip('/')/'updaterd'),'--config',str(temporary_profile),
            '--robot-socket',str(root/'run/robotd.sock'),'--token',str(state/'unused-account.json')]
        engine_env={**os.environ,'PATH':str(root/STABLE.lstrip('/')/'engine-bin')+os.pathsep+os.environ.get('PATH',''),
                    'MICRODUCK_OFFLINE_BUILD':'1' if offline else '0'}
        subprocess.run([*command,'--self-test'],check=True,env=engine_env)
        subprocess.run([*command,'install','--from',str(cache),'--component','software','--dry-run'],check=True,env=engine_env)
        if live:
            import fcntl
            live_marker.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
            live_marker.write_text('bootstrap');live_marker.chmod(0o644)
            gate_descriptor=os.open(live_marker.parent/'motion.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o644)
            os.fchmod(gate_descriptor,0o644);fcntl.flock(gate_descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # Existing browser processes are stopped before migration, so no legacy
            # recorder/servo command can begin between readiness and the first swap.
            if (root/'var/lib/microduck-studio/servo-session.json').exists() or (root/'home/radxa/studio-dev/state/servo-session.json').exists():
                raise ValueError('close the servo session before bootstrapping')
            from control import Engine,check_robot,json_socket,atomic_json
            from health import identities
            robot=Engine('/run/robotd.sock'); before=robot.call('robot.busStatus'); time.sleep(.3)
            check_robot(robot.call('robot.busStatus'),robot.call('robot.health'),before)
            audio=json_socket('/run/duck-echo/control.sock',{'method':'status'})
            if audio.get('ok') is not True or any(audio.get('status',{}).get(key) is not False for key in ('listening','music')):
                raise ValueError('turn off voice/music before bootstrapping')
            atomic_json('/run/microduck-updater/protected-services.json',identities())
            stopped=True
            subprocess.run(['systemctl','stop','microduck-studio','duck-echo'],check=True)
            before=robot.call('robot.busStatus');time.sleep(.3)
            check_robot(robot.call('robot.busStatus'),robot.call('robot.health'),before)
            legacy=root/'home/radxa/studio-dev/state'; destination=root/'var/lib/microduck-studio'
            if legacy.is_dir():
                import pwd
                account=pwd.getpwnam('radxa')
                migrate_state(legacy,destination,backup,account.pw_uid,account.pw_gid)
            subprocess.run(['usermod','-a','-G','audio,input,robot','radxa'],check=True)
        subprocess.run([*command,'install','--from',str(cache),'--component','software'],check=True,env=engine_env)
        current=root/'opt/microduck/software/current'; metadata=verify_tree(current)
        if metadata['version']!=manifest['version']: raise ValueError('engine installed an unexpected version')
        # Store points current at a relative releases/<version> link, suitable for
        # mounting the image root somewhere else and later booting it.
        for name in UNITS:
            wants=root/'etc/systemd/system/multi-user.target.wants'; wants.mkdir(parents=True,exist_ok=True)
            link=wants/name
            if not link.exists() and not link.is_symlink(): link.symlink_to('../'+name)
        if live:
            subprocess.run(['systemctl','daemon-reload'],check=True)
            subprocess.run(['systemctl','start','microduck-updater-engine','microduck-studio','duck-echo'],check=True)
            from health import wait_healthy
            wait_healthy(90)
            os.close(gate_descriptor);gate_descriptor=None;live_marker.unlink(missing_ok=True)
            subprocess.run(['systemctl','start','microduck-update-control'],check=True)
        return {'version':metadata['version'],'backup':str(backup),'offline':offline}
    except Exception:
        if live and stopped: subprocess.run(['systemctl','stop','microduck-studio','duck-echo','microduck-update-control','microduck-updater-engine'],check=False)
        for name in reversed(replaced):
            target=root/name
            if target.is_dir() and not target.is_symlink(): shutil.rmtree(target)
            elif target.exists() or target.is_symlink(): target.unlink()
            if name in original:
                saved=backup/name; target.parent.mkdir(parents=True,exist_ok=True)
                if saved.is_dir(): shutil.copytree(saved,target,symlinks=True)
                else: shutil.copy2(saved,target,follow_symlinks=False)
        if live:
            subprocess.run(['systemctl','daemon-reload'],check=False)
            if stopped: subprocess.run(['systemctl','start','microduck-studio','duck-echo'],check=False)
        raise
    finally:
        if gate_descriptor is not None:os.close(gate_descriptor)
        if live:live_marker.unlink(missing_ok=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='/'); parser.add_argument('--package',required=True)
    parser.add_argument('--public-key',required=True); parser.add_argument('--engine',required=True)
    parser.add_argument('--engine-command-json',default='[]'); parser.add_argument('--offline',action='store_true')
    args=parser.parse_args(); values=vars(args); values['engine_command']=json.loads(values.pop('engine_command_json'))
    print(json.dumps(bootstrap(**values)))
