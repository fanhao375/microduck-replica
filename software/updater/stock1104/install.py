"""Stage the signed 1104 host preparation for the next normal boot."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import zipfile

ENGINE = 'microduck-updater-engine'
CONTROL = 'microduck-update-control'
STUDIO = 'microduck-studio'
LEGACY_ASSETS = '/usr/local/lib/microduck-stock1104-preparation'
ASSETS = '/usr/local/lib/microduck-stock1104-online'
STABLE = '/usr/local/lib/microduck-updater'
SYSTEMD = '/etc/systemd/system'
SUITE = '/opt/microduck/software'
NAME = '99-stock1104-software-preparation.conf'
STUDIO_ARGS = '--listen 0.0.0.0:8000 --state-dir /var/lib/microduck-studio --log-dir /var/log/microduck-studio'
STATIC = ('settings-updates.js', 'updates-ui-state.js')
RC_VERSION = '2026.11.20-rc.1'
STABLE_VERSION = '2026.11.20'
IMAGE_MANIFEST = '/usr/share/doc/microduck/镜像清单.txt'
IMAGE_MANIFEST_SHA256 = '47935e29ef69e29024ca9171b40e97e75d765272f253e8b6d01ceadef5d4c2b4'
RC_REVISION = 'b77329b0ba1ad526b825044a625ddfe91b798b38'
# suite-version.json from the factory-key-verified rc.1 tar.zst.
RC_METADATA_SHA256 = 'c212531de0964c1debdf8e4617636f561e207c2470380ed503d87693d8970678'
# Files installed by the original signed prepare-stock-1104.pyz (acf175a7…).
LEGACY_DIGESTS = {
    'manifest.json': '049ec35aae9f285950c014d2c2c5c80aadaa179d2a109ce4229871ec0a94e3b0',
    'app.py': 'df226cbb4256a76349a308734a0c25cc5b04c2a9ffcabf5f30456cb7fd60afb3',
    'control.py': '05ca48e1493cadd2584db60ca86680a1202456db1bdcc341f47746aba1307c8d',
    'health.py': 'd2c08e3a74db6d658dfeb86e161f923513e1764dd5247ea706832c06c2378f97',
    'launch.py': 'e0efed8749adb54dad1644865495b82b386bdd3939f87df2b05644007d00f22b',
    'updates.py': 'c15f4f98de11401ebb06f26f6d857fe056efcf2cbc6fa4f47f2cd4bdad2975d4',
}

def dropins(assets=ASSETS):
    def bind(name):
        return 'BindReadOnlyPaths=' + assets + '/' + name + ':' + STABLE + '/' + name + '\n'
    return {
        ENGINE: '[Service]\nUMask=0022\n' + bind('health.py'),
        CONTROL: '[Service]\n' + bind('control.py'),
        STUDIO: '[Service]\nExecStart=\nExecStart=/usr/bin/python3 -I ' + assets + '/launch.py ' + STUDIO_ARGS + '\n',
    }

def fail(message):
    raise ValueError(message)

def directory(path, create=False):
    if not path.exists() and create:
        directory(path.parent)
        path.mkdir(mode=0o755)
        syncdir(path.parent)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        fail('目录属主或类型不符合原版安装：' + str(path))

def regular(path, limit=2*1024*1024):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or info.st_size > limit or info.st_nlink != 1):
        fail('文件类型、权限或大小不符合原版安装：' + str(path))
    return path.read_bytes()

def syncdir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def write_new(path, data):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.prepare1104-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        syncdir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)

def checked_path(root, absolute):
    relative = Path(absolute).relative_to('/')
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor/part
        directory(cursor)
    return root/relative

def original(root, path, digest):
    if hashlib.sha256(regular(checked_path(root, path))).hexdigest() != digest:
        fail('只适用于原版 1104，文件摘要不符：' + path)

def check_stock(root, manifest):
    version = manifest['version']
    current = checked_path(root, SUITE + '/current')
    link = os.readlink(current)
    revisions = {RC_VERSION:(RC_REVISION,RC_METADATA_SHA256),
                 STABLE_VERSION:(manifest['ref'],manifest['stable_manifest_sha256'])}
    if link not in ['releases/' + v for v in (version, *revisions)]:
        fail('当前软件不是原版 1104，已停止')
    current_version = link.split('/')[1]
    if current_version in revisions:
        revision, expected_metadata = revisions[current_version]
        release = SUITE + '/' + link
        data = regular(checked_path(root, release + '/suite-version.json'))
        if hashlib.sha256(data).hexdigest() != expected_metadata:
            fail(current_version + ' 与已签名发布包的清单摘要不符')
        metadata = json.loads(data)
        if metadata['version'] != current_version or metadata['source_revision'] != revision:
            fail(current_version + ' 来源与发布包不符')
        for path, digest in metadata['files'].items():
            installed = regular(checked_path(root, release + '/' + path), limit=160*1024*1024)
            if hashlib.sha256(installed).hexdigest() != digest:
                fail(current_version + ' 文件摘要不符：' + path)
    release = SUITE + '/releases/' + version
    stock_present = os.path.lexists(checked_path(root, release))
    if stock_present or current_version == version:
        meta = json.loads(regular(checked_path(root, release+'/suite-version.json')))
        if meta.get('source_revision') != manifest['stock'] or meta.get('version') != version:
            fail('当前软件来源与原版 1104 不符')
    else:
        # The engine can prune the oldest suite. The immutable factory image manifest
        # identifies a stock 1104 host independently of the software retention count.
        original(root, IMAGE_MANIFEST, IMAGE_MANIFEST_SHA256)
    for name, digest in manifest['originals'].items():
        if name in ('app.py','updates.py',*STATIC):
            if not stock_present:
                continue
            path = ('studio/web/' if name in STATIC else 'studio/studio/') + name
            if meta.get('files',{}).get(path) != digest:
                fail('安装清单中的原版 Studio 摘要不符：' + path)
            original(root, release + '/' + path, digest)
        else:
            original(root, STABLE + '/' + name, digest)
    for key, unit in (('ENGINE', ENGINE), ('CONTROL',CONTROL), ('STUDIO',STUDIO)):
        original(root, SYSTEMD+'/'+unit+'.service', manifest['units'][key])
    original(root, SYSTEMD+'/'+STUDIO+'.service.d/90-software-update.conf', manifest['units']['OVERRIDE'])
    return current_version

def payload(archive):
    names = ('app.py', 'updates.py', 'launch.py', 'health.py', 'control.py', *STATIC)
    return {'manifest.json': archive.read('manifest.json'),
            **{name: archive.read('payload/' + name) for name in names}}

def verify_legacy(target):
    if not os.path.lexists(target):
        return False
    directory(target)
    if set(os.listdir(target)) != set(LEGACY_DIGESTS):
        fail('旧准备目录的文件清单不符')
    for name, digest in LEGACY_DIGESTS.items():
        if hashlib.sha256(regular(target/name)).hexdigest() != digest:
            fail('旧准备目录与首版签名工具的摘要不符：' + name)
    return True

def switch_link(path, value):
    # Stage the symlink on the same filesystem. os.replace provides one atomic switch.
    with tempfile.TemporaryDirectory(prefix='.link1104-', dir=path.parent) as tmp:
        link = Path(tmp)/'link'
        link.symlink_to(value)
        os.replace(link, path)
        syncdir(path.parent)

def link_value(path):
    if path.lstat().st_uid != 0 or not path.is_symlink():
        fail('已有同名文件不是本工具的链接：' + str(path))
    return os.readlink(path)

def stage(root, files, *, mode):
    target = checked_path(root, ASSETS)
    legacy = verify_legacy(checked_path(root, LEGACY_ASSETS))
    present = os.path.lexists(target)
    online = dropins()
    previous = dropins(LEGACY_ASSETS)
    dormant = '# Stock service configuration\n'
    baseline, units = {}, {}
    for unit in online:
        parent = checked_path(root, SYSTEMD+'/'+unit+'.service.d')
        if os.path.lexists(parent):
            directory(parent)
        path = parent/NAME
        relative = os.path.relpath(target/'active'/(unit+'.conf'), parent)
        owned_link = False
        baseline[unit] = dormant
        if os.path.lexists(path):
            if path.is_symlink():
                if not present or link_value(path) != relative:
                    fail('已有同名链接与准备工具内容不同：' + str(path))
                owned_link = True
            elif legacy and regular(path) == previous[unit].encode():
                baseline[unit] = previous[unit]
            else:
                fail('已有同名文件与准备工具内容不同：' + str(path))
        units[unit] = (path, relative, owned_link)

    # Systemd can read a drop-in even if preparation loses power before the reboot.
    # Every unit first gets its original effective content via the baseline directory.
    # Once all three links are durable an atomic selector activates the entire set.
    configurations = {'stock': {u:dormant for u in units}, 'baseline': baseline, 'online': online}
    if present:
        directory(target)
        if set(os.listdir(target)) != set(files) | set(configurations) | {'active'}:
            fail('已有准备目录的内容不完整或有外来文件')
        for name, data in files.items():
            if regular(target/name) != data:
                fail('已有准备目录的文件摘要不符：' + name)
        active = link_value(target/'active')
        if active not in configurations:
            fail('已有准备目录的活动配置未知')
        for group, configs in configurations.items():
            folder = target/group
            directory(folder)
            if set(os.listdir(folder)) != {unit+'.conf' for unit in units}:
                fail('准备目录的服务名单不符：' + group)
            for unit, config in configs.items():
                actual = regular(folder/(unit+'.conf'))
                if group == 'baseline':
                    allowed = [dormant.encode()]
                    if legacy:
                        allowed.append(previous[unit].encode())
                else:
                    allowed = [config.encode()]
                if actual not in allowed:
                    fail('准备目录的服务配置不符：' + unit)
                path, _, linked = units[unit]
                if active == 'baseline' and group == 'baseline' and not linked:
                    original_config = regular(path) if os.path.lexists(path) else dormant.encode()
                    if actual != original_config:
                        fail('服务与原有准备配置不一致：' + unit)
        if active == 'online' and not all(linked for _,_,linked in units.values()):
            fail('已启用的在线准备配置不完整')
    else:
        active = 'baseline'
    if mode == 'check':
        return '已完整暂存' if active == 'online' else '原版 1104 主机文件已核对，可暂存准备工具'
    if mode == 'restore' and not present and not any(os.path.lexists(p) for p,_,_ in units.values()):
        return '当前已使用原版服务配置。'
    if not present:
        with tempfile.TemporaryDirectory(prefix='.stage1104-', dir=target.parent) as temporary:
            staged = Path(temporary)/'files'
            staged.mkdir(mode=0o755)
            for name,data in files.items():
                write_new(staged/name,data)
            for group, configs in configurations.items():
                folder = staged/group
                folder.mkdir()
                folder.chmod(0o755)
                for unit, config in configs.items():
                    write_new(folder/(unit+'.conf'), config.encode())
            (staged/'active').symlink_to('baseline')
            staged.chmod(0o755)
            syncdir(staged)
            os.rename(staged, target)
            syncdir(target.parent)
    for path, relative, linked in units.values():
        if not linked:
            directory(path.parent, create=True)
            switch_link(path, relative)
    desired = 'stock' if mode == 'restore' else 'online'
    if active != desired:
        switch_link(target/'active', desired)
    if mode == 'restore':
        for path, _, _ in units.values():
            path.unlink()
            syncdir(path.parent)
        return '已撤销准备工具的服务配置。下次正常启动将使用原版主机更新器。'
    return '主机准备已暂存。请正常重启，然后在 Studio 设置中打开软件更新。'

@contextmanager
def motion_lock():
    import fcntl
    fd = os.open('/run/microduck-updater/motion.lock', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)

def live_idle():
    from control import Engine, json_socket, require_model_idle
    if os.path.lexists('/run/microduck-updater/installing'):
        fail('软件更新进行中，请先结束该事务')
    control = json_socket('/run/microduck-updater/control.sock', {'op':'status'})
    if control.get('ok') is not True or control.get('busy') is not False or control.get('engine_available') is not True:
        fail('软件更新器忙或未就绪')
    states = Engine('/run/microduck-updater/engine.sock').call('update.status')
    software = [row for row in states if row.get('component')=='software']
    if len(software)!=1 or software[0].get('phase') != 'idle':
        fail('软件签名引擎正在处理事务或状态未知')
    ready = json_socket('/run/microduck-studio/update.sock', {'op':'readiness'})
    if ready.get('ready') is not True:
        fail('请先结束原版 Studio 的标定、舵机会话或驱动更新：' + str(ready.get('error','状态未知')))
    require_model_idle(Engine('/run/robotd.sock'))
    command = subprocess.check_output(['/usr/bin/systemctl','show', STUDIO+'.service',
                                       '--property=ExecStart','--value'], text=True, timeout=5)
    programs = ('run.py', ASSETS+'/launch.py', LEGACY_ASSETS+'/launch.py')
    commands = ['/usr/bin/python3 -I '+program+' ' for program in programs]
    if command.count('argv[]=') != 1 or not any('argv[]='+prefix+STUDIO_ARGS+' ;' in command for prefix in commands):
        fail('Studio 启动参数有自定义变更，无法应用原版准备工具')

def active_views(files, version):
    # Verify what the running service sees through its private mount namespace.
    views = {}
    for unit, name in ((CONTROL,'control.py'), (ENGINE,'health.py')):
        pid = subprocess.check_output(['/usr/bin/systemctl','show',unit,'-p','MainPID','--value'],text=True,timeout=5).strip()
        if not pid.isdecimal() or int(pid) <= 0:
            return False
        proc = Path('/proc')/pid
        views[unit] = (proc/'root'/STABLE.lstrip('/')/name).read_bytes() == files[name]
        if unit == ENGINE:
            views['umask'] = any(line.split()==['Umask:', '0022'] for line in (proc/'status').read_text().splitlines())
    from control import json_socket
    control = json_socket('/run/microduck-updater/control.sock', {'op':'status'})
    if version not in (RC_VERSION, STABLE_VERSION):
        pid = subprocess.check_output(['/usr/bin/systemctl','show',STUDIO,'-p','MainPID','--value'],text=True,timeout=5).strip()
        if not pid.isdecimal() or int(pid) <= 0:
            return False
        views[STUDIO] = (Path('/proc')/pid/'cmdline').read_bytes().split(b'\0')[:3] == [
            b'/usr/bin/python3', b'-I', (ASSETS+'/launch.py').encode()]
    ready = json_socket('/run/microduck-studio/update.sock', {'op':'software-readiness'})
    return all(views.values()) and control.get('online_update') is True and ready.get('ready') is True

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--restore-stock', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        fail('需要 root 权限核对主机与更新事务')
    with zipfile.ZipFile(sys.argv[0]) as archive:
        files = payload(archive)
    with motion_lock():
        version = check_stock(Path('/'), json.loads(files['manifest.json']))
        if args.restore_stock and version in (RC_VERSION, STABLE_VERSION):
            fail('请先通过 Studio 回到原版 2026.11.4，再撤销主机准备')
        live_idle()
        message = stage(Path('/'),files,mode='check' if args.check else 'restore' if args.restore_stock else 'prepare')
        if args.check and active_views(files,version):
            message += '。当前 Studio、协调器及签名引擎的主机修复已生效。'
        if version == RC_VERSION:
            message += ' 当前 rc.1 请在 Studio 依次检查、下载、安装正式版。'
        print(message)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('1104 主机准备未完成：' + str(error), file=sys.stderr)
        sys.exit(1)
