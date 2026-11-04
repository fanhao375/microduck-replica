"""Materialize a verified controller pair's fixed support files in an offline image.

Call only after the shared signed software bootstrap has completed. Native
binaries are supplied by the separately verified image payload and must already
match the signed suite. This module never starts services or contacts a device.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import types

PAIR = ('studio/deploy/native/padd','studio/deploy/native/controller-manifest.json')
MODULES = ('studio/deploy/native_update.py','studio/deploy/controller_bundle.py')
NATIVE = ('studio/deploy/native/robotd','studio/deploy/native/manifest.json')
VOICE = ('duck-echo/duck_echo.py','duck-echo/pad_mapping.py')
SERVICES = ('padd','microduck-studio','duck-echo','mediad')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def target(root, relative, *, trusted=True):
    """Walk image paths without accepting symlinks or writable owned parents."""
    if (not isinstance(relative,str) or not relative or '\\' in relative or ':' in relative
            or any(part in ('','.','..') for part in relative.split('/'))):
        raise ValueError('unsafe factory path')
    path = root
    parts = relative.split('/')
    for index,part in enumerate(parts):
        path = path/part
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or (index<len(parts)-1 and not stat.S_ISDIR(info.st_mode))
                or (trusted and os.name=='posix' and (info.st_uid!=0 or info.st_mode&0o022))):
            raise ValueError('unsafe factory path or symlink: '+relative)
    return path


def read(root, relative, limit, *, trusted=True):
    path = target(root,relative,trusted=trusted)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=limit:
            raise ValueError('factory component is not a bounded regular file: '+relative)
        fd = os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as source:
            data = source.read(limit+1)
        if len(data)>limit:
            raise ValueError('factory component exceeds bound: '+relative)
        return data
    except OSError as error:
        raise ValueError('factory component missing or unreadable: '+relative) from error


def frozen_module(name, data):
    module = types.ModuleType('factory_verified_'+name.replace('.','_'))
    module.__file__ = name
    exec(compile(data,name,'exec'),module.__dict__)
    return module


def support_files(bundle):
    """Only the four declared service drop-ins and immutable gate are writable."""
    dropin = bundle.DROPIN
    if not isinstance(dropin,str) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*\.conf',dropin):
        raise ValueError('unsafe controller drop-in declaration')
    expected = {'/etc/systemd/system/'+name+'.service.d/'+dropin for name in SERVICES}
    if (not isinstance(bundle.UNITS,dict) or set(bundle.UNITS)!=expected
            or bundle.VOICE_GUARD!='/usr/local/lib/microduck-controller-update/voice-guard.py'
            or bundle.COMPAT!='/var/lib/microduck-controller-update/compatibility.json'):
        raise ValueError('unsafe controller support path declaration')
    files = {**bundle.UNITS,bundle.VOICE_GUARD:bundle.VOICE_GUARD_PROGRAM,bundle.COMPAT:bundle.COMPAT_BYTES}
    if any(not isinstance(data,bytes) or not 0<len(data)<=512*1024 for data in files.values()):
        raise ValueError('invalid controller support file declaration')
    return files


def write(root, relative, data):
    path = target(root,relative)
    parent = root
    for part in relative.split('/')[:-1]:
        parent = parent/part
        if not parent.exists():
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
            if os.name=='posix':
                os.chown(parent,0,0)
    fd, temporary = tempfile.mkstemp(prefix='.controller-factory-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush()
            if os.name=='posix':
                os.fchown(stream.fileno(),0,0)
                os.fchmod(stream.fileno(),0o644)
            else:
                os.chmod(temporary,0o644)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('unsafe offline factory rootfs symlink')
    root = root.resolve(strict=True)
    if root==Path(root.anchor) or not target(root,'etc',trusted=False).is_dir():
        raise ValueError('offline factory rootfs required')
    software = target(root,'opt/microduck/software',trusted=False)
    current = software/'current'
    if not current.is_symlink():
        raise ValueError('verified installed software release required')
    link = os.readlink(current).replace('\\','/')
    if not re.fullmatch(r'releases/[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?',link):
        raise ValueError('unsafe installed software release link')
    relative = 'opt/microduck/software/'+link
    suite = json.loads(read(root,relative+'/suite-version.json',4*1024*1024,trusted=False))
    version = link.split('/')[1]
    if (not isinstance(suite,dict) or suite.get('version')!=version
            or not isinstance(suite.get('files'),dict) or len(suite['files'])>20000):
        raise ValueError('invalid installed suite provenance')
    declared = suite['files']
    if not any(name in declared or os.path.lexists(root/(relative+'/'+name)) for name in PAIR):
        return None
    target(root,relative+'/suite-version.json')
    contracts = suite.get('contracts')
    if not isinstance(contracts,dict) or type(contracts.get('padd_mapping')) is not int or contracts['padd_mapping']!=1:
        raise ValueError('controller pair requires the voice mapping contract')
    verified = {}
    for name in (*MODULES,*NATIVE,*PAIR,*VOICE):
        limit = 32*1024*1024 if name.rsplit('/',1)[-1] in ('robotd','padd') else 512*1024
        data = read(root,relative+'/'+name,limit)
        if sha(data)!=declared.get(name):
            raise ValueError('installed controller suite hash mismatch: '+name)
        verified[name] = data
    # No managed code is compiled until every dependency above has its signed
    # suite hash checked. These same frozen validators are used by the publisher.
    try:
        native, bundle = [frozen_module(name,verified[name]) for name in MODULES]
        robot, pad = verified[NATIVE[0]], verified[PAIR[0]]
        manifest = json.loads(verified[PAIR[1]])
        legacy = json.loads(verified[NATIVE[1]])
        bundle.validate_bundle(robot,pad,manifest,version)
        native.validate_payload(robot,legacy,version)
        if (not bundle.compatible_suite(suite,verified) or any(
                manifest['components']['robotd'].get(key)!=legacy.get(key)
                for key in ('sha256','size','source_revision','build','ipc_api'))):
            raise ValueError('controller component or voice contract mismatch')
        if bundle.ROBOTD!='/opt/robot/feetech-ft5-r5/robotd' or bundle.PADD!='/opt/robot/feetech-ft5-r5/padd':
            raise ValueError('unsafe controller binary path declaration')
        for name,data in ((bundle.ROBOTD,robot),(bundle.PADD,pad)):
            if read(root,name.lstrip('/'),32*1024*1024)!=data:
                raise ValueError('image component does not match signed controller payload: '+name)
        files = support_files(bundle)
    except (AttributeError,KeyError,TypeError,SyntaxError) as error:
        raise ValueError('invalid frozen controller component declaration') from error
    # Preflight the whole write set before creating even one support directory.
    for name,data in files.items():
        path = target(root,name.lstrip('/'))
        if path.exists() and read(root,name.lstrip('/'),512*1024)!=data:
            raise ValueError('existing controller support file differs: '+name)
        if os.name=='posix':
            for parent in path.parents:
                if parent==root:
                    break
                if parent.exists() and parent.stat().st_mode&0o005!=0o005:
                    raise ValueError('controller support parent is not publicly readable: '+name)
    if os.name=='posix' and os.geteuid()!=0:
        raise ValueError('offline controller factory installation requires root ownership')
    for name,data in files.items():
        write(root,name.lstrip('/'),data)
    return {'schema':bundle.PAYLOAD_SCHEMA,'version':version,
            'components':{'robotd':sha(robot),'padd':sha(pad)},
            'files':{name:sha(data) for name,data in files.items()}}
