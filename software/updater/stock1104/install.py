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
ASSETS = '/usr/local/lib/microduck-stock1104-preparation'
STABLE = '/usr/local/lib/microduck-updater'
SYSTEMD = '/etc/systemd/system'
SUITE = '/opt/microduck/software'
NAME = '99-stock1104-software-preparation.conf'
STUDIO_ARGS = '--listen 0.0.0.0:8000 --state-dir /var/lib/microduck-studio --log-dir /var/log/microduck-studio'

def dropins():
    def bind(name):
        return 'BindReadOnlyPaths=' + ASSETS + '/' + name + ':' + STABLE + '/' + name + '\n'
    return {
        ENGINE: '[Service]\nUMask=0022\n' + bind('health.py'),
        CONTROL: '[Service]\n' + bind('control.py'),
        STUDIO: '[Service]\nExecStart=\nExecStart=/usr/bin/python3 -I ' + ASSETS + '/launch.py ' + STUDIO_ARGS + '\n',
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
    if os.readlink(current) != 'releases/' + version:
        fail('当前软件不是原版 1104，已停止')
    release = SUITE + '/releases/' + version
    meta = json.loads(regular(checked_path(root, release+'/suite-version.json')))
    if meta.get('source_revision') != manifest['stock'] or meta.get('version') != version:
        fail('当前软件来源与原版 1104 不符')
    for name, digest in manifest['originals'].items():
        if name in ('app.py','updates.py'):
            path = 'studio/studio/' + name
            if meta.get('files',{}).get(path) != digest:
                fail('安装清单中的原版 Studio 摘要不符：' + path)
            original(root, release + '/' + path, digest)
        else:
            original(root, STABLE + '/' + name, digest)
    for key, unit in (('ENGINE', ENGINE), ('CONTROL',CONTROL), ('STUDIO',STUDIO)):
        original(root, SYSTEMD+'/'+unit+'.service', manifest['units'][key])
    original(root, SYSTEMD+'/'+STUDIO+'.service.d/90-software-update.conf', manifest['units']['OVERRIDE'])

def existing(path, data):
    if not os.path.lexists(path):
        return False
    if regular(path) != data:
        fail('已有同名文件与准备工具内容不同：' + str(path))
    return True

def payload(archive):
    names = ('app.py', 'updates.py', 'launch.py', 'health.py', 'control.py')
    return {'manifest.json': archive.read('manifest.json'),
            **{name: archive.read('payload/' + name) for name in names}}

def stage(root, files, *, mode):
    target = checked_path(root, ASSETS)
    present = os.path.lexists(target)
    if present:
        directory(target)
        if set(os.listdir(target)) != set(files) or not all(existing(target/name, data) for name, data in files.items()):
            fail('已有准备目录的内容不完整或有外来文件')
    units = []
    for unit, text in dropins().items():
        parent = checked_path(root, SYSTEMD+'/'+unit+'.service.d')
        if os.path.lexists(parent):
            directory(parent)
        path = parent/NAME
        units.append((path, text.encode(), existing(path,text.encode())))
    if mode == 'check':
        return '已完整暂存' if present and all(item[2] for item in units) else '原版 1104 文件已核对，可暂存准备工具'
    if mode == 'restore':
        for path, _, found in units:
            if found:
                path.unlink()
                syncdir(path.parent)
        return '已撤销本工具的服务配置。下次正常启动将使用原版主机更新器。'
    if not present:
        with tempfile.TemporaryDirectory(prefix='.stage1104-', dir=target.parent) as temporary:
            staged = Path(temporary)/'files'
            staged.mkdir(mode=0o755)
            for name,data in files.items():
                write_new(staged/name,data)
            staged.chmod(0o755)
            os.rename(staged, target)
            syncdir(target.parent)
    changed = []
    try:
        for path, data, found in units:
            if not found:
                directory(path.parent, create=True)
                write_new(path, data)
                changed.append(path)
    except Exception:
        for path in reversed(changed):
            path.unlink()
            syncdir(path.parent)
        raise
    return '主机准备已暂存。请正常重启，然后在 Studio 上传并验证软件更新包。'

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
    commands = ('/usr/bin/python3 -I run.py ', '/usr/bin/python3 -I '+ASSETS+'/launch.py ')
    if command.count('argv[]=') != 1 or not any('argv[]='+prefix+STUDIO_ARGS+' ;' in command for prefix in commands):
        fail('Studio 启动参数有自定义变更，无法应用原版准备工具')

def active_views(files):
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
    ready = json_socket('/run/microduck-studio/update.sock', {'op':'software-readiness'})
    return all(views.values()) and ready.get('ready') is True

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
        check_stock(Path('/'), json.loads(files['manifest.json']))
        live_idle()
        message = stage(Path('/'),files,mode='check' if args.check else 'restore' if args.restore_stock else 'prepare')
        if args.check and active_views(files):
            message += '。当前 Studio、协调器及签名引擎的主机修复已生效。'
        print(message)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('1104 主机准备未完成：' + str(error), file=sys.stderr)
        sys.exit(1)
