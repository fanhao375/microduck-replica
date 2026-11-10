"""Fixed controller companion contract and explicit root transaction.

Publisher imports declarations/pure manifest functions from frozen Git bytes.
Runtime dependencies are passed by the suite-hash-verifying helper loader.
"""
import hashlib
import json
import math
import os
import re
import socket
import stat
import time
import types
import uuid

PAYLOAD_SCHEMA='microduck-controller-payload/1'
SCHEMA='microduck-controller-update/1'
ROBOTD_SHA256='f8c720a3acf2eb5e6c844ba041d79d7312f49a50e99c04182c5ec0dc4bb9ac9e'
ROBOTD_SIZE=7065952
ROBOTD_SOURCE='56046c5aa10be4de12d4389ae0463e4f129df940'
ROBOTD_BUILD='replica-imu-home-control.50'
LEGACY_ROBOTD_SHA256='725b74cd93660a312970a44879abe5b75e8becf108499a1b8798961e48ca5f00'
VERIFY_ROBOTD_SHA256='e5ff7fb429fd0dfb762eb4da1bcc5a26b48b6d2dfdae4bb4904213e37e022ed0'
PREDECESSOR_PROCEDURE='已完成的 {} 配套驱动请先明确确认“恢复旧配套驱动”，再安装本版；操作说明：docs/native-update-20261104.md'
# Frozen from two independent, byte-identical builds and actual QEMU status.
PADD_SHA256='dc1c7043a108964583fc09a11e3d6f87a25e259c346f7ce9154e77500a0881ec'
PADD_SIZE=3522648
PADD_SOURCE='665613ac628fdc9b1b71f324809f0c16ce2795f2'
SUPPORTED_PADD_SHA256=('85fc8051df2ce496abf32bbb5eba3c94c44d875365a8f33a2f53f90facba90db',)
ROBOTD='/opt/robot/feetech-ft5-r5/robotd'
PADD='/opt/robot/feetech-ft5-r5/padd'
MEDIAD='/opt/robot/feetech-ft5-r5/mediad'
MEDIAD_SHA256='0417c8c2dc5a63dd2e1c2a65d652f31609c232abab95d50e4acbd1faab0f742b'
STATE='/var/lib/microduck-helper/controller-update'
JOURNAL=STATE+'/journal.json'
PREPARED=STATE+'/prepared.json'
PUBLIC_STATE='/var/lib/microduck-controller-update'
COMPAT=PUBLIC_STATE+'/compatibility.json'
VOICE_GUARD='/usr/local/lib/microduck-controller-update/voice-guard.py'
DROPIN='zzzzzz-microduck-controller-bundle.conf'
UNITS={
 '/etc/systemd/system/padd.service.d/'+DROPIN:b'[Service]\nStateDirectory=padd\nStateDirectoryMode=0750\n',
 '/etc/systemd/system/microduck-studio.service.d/'+DROPIN:b'[Service]\nSupplementaryGroups=robot\n',
 '/etc/systemd/system/duck-echo.service.d/'+DROPIN:(
     '[Service]\nSupplementaryGroups=robot\nExecStartPre=/usr/bin/python3 -I '+VOICE_GUARD+'\n').encode(),
 '/etc/systemd/system/mediad.service.d/'+DROPIN:
     b'[Service]\nRestrictAddressFamilies=\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK\n',
}
VOICE_FILES=('duck-echo/pad_mapping.py','duck-echo/duck_echo.py')
PHASES=frozenset(('prepared','guarding','replacing','verifying','restoring','completed','restored','recovery_required'))
RESTORE_IMMUTABLE=('schema','bundle_schema','transaction_id','mode','target_robotd','target_padd',
    'old_sha256','target_sha256','records','files','service_files','targets','services','controllers',
    'original_services','original_process','padd_args','profile_sha256','protected')


def sha(data):return hashlib.sha256(data).hexdigest()


def bundle_manifest(version,robotd_bytes,padd_bytes):
    if not isinstance(version,str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?',version):
        raise ValueError('invalid software version')
    return {'schema':PAYLOAD_SCHEMA,'architecture':'aarch64','software_version':version,
        'components':{
            'robotd':{'sha256':sha(robotd_bytes),'size':len(robotd_bytes),'source_revision':ROBOTD_SOURCE,'build':ROBOTD_BUILD,'ipc_api':44},
            'padd':{'sha256':sha(padd_bytes),'size':len(padd_bytes),'source_revision':PADD_SOURCE,'mapping_api':1,
                    'supported_installed_sha256':list(SUPPORTED_PADD_SHA256)}},
        'units':{path:{'sha256':sha(body),'mode':0o644} for path,body in UNITS.items()},
        'voice_contract':{'padd_mapping':1}}


def validate_bundle(robotd_bytes,padd_bytes,manifest,version):
    def elf(data):return (len(data)>=64 and data[:7]==b'\x7fELF\x02\x01\x01'
        and data[16:18] in (b'\x02\x00',b'\x03\x00') and data[18:20]==b'\xb7\x00')
    if (not isinstance(PADD_SHA256,str) or not re.fullmatch('[0-9a-f]{64}',PADD_SHA256)
            or not isinstance(PADD_SOURCE,str) or not re.fullmatch('[0-9a-f]{40}',PADD_SOURCE)
            or type(PADD_SIZE) is not int or not 64<=PADD_SIZE<=32*1024*1024
            or not elf(robotd_bytes) or not elf(padd_bytes)
            or (sha(robotd_bytes),len(robotd_bytes))!=(ROBOTD_SHA256,ROBOTD_SIZE)
            or (sha(padd_bytes),len(padd_bytes))!=(PADD_SHA256,PADD_SIZE)
            or manifest!=bundle_manifest(version,robotd_bytes,padd_bytes)):
        raise ValueError('controller bundle is not the frozen reviewed component vector')


def compatible_suite(metadata,files):
    contract=(metadata.get('contracts') or {}).get('padd_mapping')
    return (type(contract) is int and contract==1 and all(name in files and
            sha(files[name])==(metadata.get('files') or {}).get(name) for name in VOICE_FILES))


def mapping_status(path):
    """Bounded real native capability probe; no token or output-producing op."""
    deadline=time.monotonic()+.8
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(.8);connection.connect(path);connection.sendall(b'{"op":"status"}\n')
        data=bytearray()
        while b'\n' not in data:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('mapping status deadline')
            connection.settimeout(remaining);chunk=connection.recv(8192)
            if not chunk:raise ValueError('mapping status connection closed')
            data.extend(chunk)
            if len(data)>262144:raise ValueError('mapping status exceeded bound')
        return json.loads(data.split(b'\n',1)[0])


# A self-contained, immutable root-owned startup gate, deliberately outside the
# software release symlink. No managed module imports and no device arguments.
VOICE_GUARD_PROGRAM=r'''"""Root-owned legacy voice compatibility startup gate."""
import hashlib,json,os,stat,sys
from pathlib import Path

def guard(root='/'):
    base=Path(root)
    def read(path,limit=4*1024*1024):
        parts=path.strip('/').split('/')
        for index in range(1,len(parts)+1):
            target=base.joinpath(*parts[:index]);info=target.lstat()
            kind=stat.S_ISREG if index==len(parts) else stat.S_ISDIR
            if not kind(info.st_mode) or (os.name=='posix' and (info.st_uid!=0 or info.st_mode&0o022)):
                raise ValueError('untrusted compatibility input')
        if info.st_size>limit:raise ValueError('oversize compatibility input')
        fd=os.open(target,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as source:return source.read(limit+1)
    try:
        marker='/var/lib/microduck-controller-update/compatibility.json'
        # lexists() also returns false on EACCES. Only a real ENOENT after
        # walking trusted parents means the compatibility requirement is absent.
        try:raw=read(marker,8192)
        except FileNotFoundError:return 0
        state=json.loads(raw)
        if state.get('schema')!='microduck-controller-compat/1' or state.get('required') is not True:return 1
        current=base/'opt/microduck/software/current';release=current.resolve()
        if not current.is_symlink() or release.parent!=(base/'opt/microduck/software/releases').resolve():return 1
        relative='/'+release.relative_to(base.resolve()).as_posix()
        suite=json.loads(read(relative+'/suite-version.json'))
        contract=(suite.get('contracts') or {}).get('padd_mapping')
        if type(contract) is not int or contract!=1:return 1
        for name in ('duck-echo/pad_mapping.py','duck-echo/duck_echo.py'):
            if hashlib.sha256(read(relative+'/'+name)).hexdigest()!=(suite.get('files') or {}).get(name):return 1
        return 0
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):return 1

if __name__=='__main__':raise SystemExit(guard() if len(sys.argv)==1 else 1)
'''.encode()

COMPAT_BYTES=b'{"schema":"microduck-controller-compat/1","required":true}\n'
SERVICES=('padd','gamesir-xboxd','duck-echo','microduck-studio','mediad')
RUNTIME='/run/padd/mapping-state.json'
PROFILE='/var/lib/padd/mappings.json'
PAD_SETTINGS='/etc/robot/feetech-ft5/pad-settings.json'
KNOWN_OVERRIDES={
    'padd':{'zzzz-r17-0151.conf','zzzz-replica-padd.conf'},
    'microduck-studio':{'10-image.conf','90-software-update.conf'},
    'duck-echo':set(),'mediad':{'40-microduck-camera-demand.conf','50-microduck-camera-network.conf'}}
PUBLIC=('active','phase','transaction_id','available','running_verified','reason','mode',
        'bundle_schema','bundle_current','components','restorable')


class BundleTransaction:
    """A distinct journal; legacy primitives retain their explicit robotd scope.

    No Transaction subclass: its journal/target methods close over v1 globals.
    Only filesystem, readiness, config, original robotd identity and safety
    checks are delegated. Vector writes/recovery/ownership are defined here.
    """
    def __init__(self,helper,op,release,legacy):
        self.h,self.op,self.ctx,self.release,self.legacy=helper,op,op.ctx,release,legacy
        self.base=legacy.Transaction(helper,op,release)

    def refuse(self,reason,code='controller_refused'):
        raise self.h.Stop(self.h.EXIT_STATE,code,reason)

    def read(self,path,limit=32*1024*1024):return self.base.read(path,limit)
    def present(self,path):return self.base.present(path)
    def write(self,path,data,mode):return self.base.write(path,data,mode)
    def directory(self,path,mode=0o755):
        parts=path.strip('/').split('/')
        missing=['/'+'/'.join(parts[:i]) for i in range(1,len(parts)+1) if not self.present('/'+'/'.join(parts[:i]))]
        self.base.directory(path,mode)
        # mkdir's mode is filtered by the caller's umask. Set explicit modes
        # only on directories just created and owned by this root transaction.
        for name in missing:
            os.chmod(self.ctx.p(name),mode if name==path else 0o755)
            self.base.sync_dir(os.path.dirname(name))

    def public_parents(self,path):
        parts=path.strip('/').split('/')
        for i in range(1,len(parts)):
            try:info=os.lstat(self.ctx.p('/'+'/'.join(parts[:i])))
            except FileNotFoundError:return
            if (not stat.S_ISDIR(info.st_mode) or (os.name=='posix' and
                    (info.st_mode&0o005!=0o005 or info.st_mode&0o022 or (self.ctx.check_owner and info.st_uid!=0)))):
                self.refuse('公开状态或兼容门的父目录不能由服务账户安全读取')
    def unit(self,name):return self.base.unit(name)

    def payload(self):
        base=self.release+'/studio/deploy/native/'
        try:
            robot,pad=self.read(base+'robotd'),self.read(base+'padd')
            manifest=json.loads(self.read(base+'controller-manifest.json',16384))
            validate_bundle(robot,pad,manifest,self.release.rsplit('/',1)[-1])
            # Both signed formats coexist. A v2 pair cannot hide a broken v1
            # robotd member from the publisher/helper's exact legacy verifier.
            self.legacy.validate_payload(robot,json.loads(self.read(base+'manifest.json',8192)),self.release.rsplit('/',1)[-1])
            return {ROBOTD:robot,PADD:pad,**UNITS,VOICE_GUARD:VOICE_GUARD_PROGRAM,COMPAT:COMPAT_BYTES}
        except (OSError,ValueError,TypeError,KeyError):
            self.refuse('伴随升级包缺件或不属于已审核组件，不能退回单独 robotd 安装','controller_payload_invalid')

    def paths(self):return (ROBOTD,PADD,*UNITS,VOICE_GUARD,COMPAT)

    def targets(self,robotd=None):
        return {ROBOTD:{'sha256':ROBOTD_SHA256 if robotd is None else robotd,'mode':0o755},PADD:{'sha256':PADD_SHA256,'mode':0o755},
                **{p:{'sha256':sha(b),'mode':0o644} for p,b in {**UNITS,VOICE_GUARD:VOICE_GUARD_PROGRAM,COMPAT:COMPAT_BYTES}.items()}}

    def snapshot(self,path):
        if not self.present(path):return {'path':path,'existed':False,'sha256':None,'mode':None,'uid':None,'gid':None}
        data=self.read(path)
        info=os.lstat(self.ctx.p(path))
        return {'path':path,'existed':True,'sha256':sha(data),'mode':stat.S_IMODE(info.st_mode),'uid':info.st_uid,'gid':info.st_gid}

    def save(self,m,phase,**fields):
        m.update(phase=phase,**fields)
        raw=(json.dumps(m,ensure_ascii=False,allow_nan=False)+'\n').encode()
        self.public_parents(JOURNAL)
        if phase=='prepared':
            # Two-file publication: first persist the complete recoverable
            # intent, then the exclusion recognized by old helpers, then expose
            # v2 as active. A crash at any following boundary is recoverable.
            self.write(PREPARED,raw,0o644)
            self.directory(self.legacy.STATE)
            self.write(self.legacy.ACTIVE,m['transaction_id'].encode(),0o644)
        self.write(JOURNAL,raw,0o644)
        if self.present(PREPARED):
            self.h.remove(self.ctx,PREPARED);self.base.sync_dir(STATE)
        self.op.emit('native','info',phase,{key:m.get(key) for key in PUBLIC if key in m})

    def load(self):
        if self.present(PREPARED):
            prepared=self.load_record(PREPARED)
            if not self.present(JOURNAL):return prepared
            committed=self.load_record(JOURNAL,prepared)
            if committed['transaction_id']==prepared['transaction_id']:
                immutable=('schema','bundle_schema','mode','target_robotd','target_padd','old_sha256','target_sha256',
                    'records','files','service_files','targets','services','controllers','original_services','original_process',
                    'padd_args','profile_sha256','protected')
                if any(committed.get(k)!=prepared.get(k) for k in immutable):self.refuse('准备记录与已发布记录不属于同一事务','controller_tampered')
                return committed
            if (committed['phase']!='restored' or committed['active'] or committed['restore_pending'] or committed['cleanup_pending']):
                self.refuse('新准备记录覆盖了尚未恢复的另一事务','controller_tampered')
            return prepared
        return self.load_record(JOURNAL)

    def load_record(self,path,prepared=None):
        if not self.present(path):return None
        try:
            m=json.loads(self.read(path,65536))
            if path==PREPARED and (m.get('phase')!='prepared' or m.get('active') is not True
                    or m.get('restore_pending') is not False or m.get('cleanup_pending') is not False
                    or (self.present(self.legacy.ACTIVE) and self.read(self.legacy.ACTIVE,128)!=m.get('transaction_id','').encode())):
                raise ValueError('untrusted controller intent')
            if (m.get('schema')!=SCHEMA or m.get('phase') not in PHASES
                    or type(m.get('active')) is not bool or not re.fullmatch('[0-9a-f]{32}',m.get('transaction_id',''))
                    or m.get('mode')!='motion' or [r['path'] for r in m['records']]!=list(self.paths())
                    or set(m['services'])!=set(SERVICES) or any(s not in ('active','inactive','failed','absent') for s in m['services'].values())
                    or m.get('target_robotd') not in (ROBOTD_SHA256,LEGACY_ROBOTD_SHA256,VERIFY_ROBOTD_SHA256) or m.get('target_padd')!=PADD_SHA256
                    or type(m.get('restore_pending')) is not bool or type(m.get('cleanup_pending')) is not bool
                    or type(m.get('running_verified')) is not bool or m.get('bundle_schema')!=PAYLOAD_SCHEMA
                    or m.get('targets')!=self.targets(m.get('target_robotd'))
                    or (m.get('profile_sha256') is not None and not re.fullmatch('[0-9a-f]{64}',m['profile_sha256']))
                    or m.get('old_sha256')!=m['records'][0]['sha256'] or m.get('target_sha256')!=m.get('target_robotd')
                    or m.get('controllers')!={u:m['services'][u]=='active' for u in self.legacy.CONTROLLERS}
                    or set(m.get('original_services',{}))!={u for u,s in m['services'].items() if s=='active'}
                    or any(m['services'][u]!='active' for u in ('padd','microduck-studio','duck-echo'))
                    or (m.get('reason') is not None and (not isinstance(m['reason'],str) or len(m['reason'])>300))):
                raise ValueError('controller journal schema mismatch')
            self.validate_pad_arguments(m['padd_args'])
            if [row.get('path') for row in m['files']]!=[*self.legacy.USER_FILES,PAD_SETTINGS]:raise ValueError('user file paths changed')
            for row in m['files']:
                if (type(row.get('existed')) is not bool or (row['existed'] and not re.fullmatch('[0-9a-f]{64}',row.get('sha256','')))
                        or (not row['existed'] and row.get('sha256') is not None)):raise ValueError('user file metadata changed')
            allowed={parent+'/'+u+'.service' for u in KNOWN_OVERRIDES for parent in ('/etc/systemd/system','/lib/systemd/system','/usr/lib/systemd/system')}
            allowed|={'/etc/systemd/system/'+u+'.service.d/'+name for u,names in KNOWN_OVERRIDES.items() for name in names}
            allowed.add(MEDIAD)
            if (not isinstance(m['service_files'],list) or len(m['service_files'])>16
                    or len({r['path'] for r in m['service_files']})!=len(m['service_files'])
                    or any(r['path'] not in allowed for r in m['service_files'])):raise ValueError('service paths changed')
            runtime=m.get('runtime_owned')
            if runtime is not None and (set(runtime)!={'pid','sha256'} or type(runtime['pid']) is not int or runtime['pid']<=0
                    or not re.fullmatch('[0-9a-f]{64}',runtime['sha256'])):raise ValueError('invalid runtime ownership')
            for index,row in enumerate(m['records']):
                if type(row['existed']) is not bool:raise ValueError('invalid preimage')
                if row['existed']:
                    if (not re.fullmatch('[0-9a-f]{64}',row['sha256']) or type(row['mode']) is not int
                            or not 0<=row['mode']<=0o777 or type(row['uid']) is not int or type(row['gid']) is not int
                            or sha(self.read(STATE+'/'+m['transaction_id']+'/component-'+str(index)))!=row['sha256']):
                        raise ValueError('controller preimage changed')
                elif any(row.get(key) is not None for key in ('sha256','mode','uid','gid')):
                    raise ValueError('invalid absent preimage')
            if m['records'][0]['sha256'] not in (*self.legacy.SUPPORTED_SHA256,ROBOTD_SHA256):raise ValueError('unknown old robotd')
            if m['records'][1]['sha256'] not in SUPPORTED_PADD_SHA256:raise ValueError('unknown old padd')
            if any(row['existed'] for row in m['records'][2:]):raise ValueError('owned path preimage was not empty')
            if m['target_robotd']!=ROBOTD_SHA256:self.legacy_terminal(m,prepared)
            elif 'restore_origin_sha256' in m or 'restore_updater_sha256' in m:self.owned_restore_origin(m)
            return m
        except (OSError,ValueError,TypeError,KeyError,AttributeError):
            self.refuse('伴随升级记录或备份无效，保留保护并恢复','controller_tampered')

    def predecessor_build(self, m):
        return '.48' if m['target_robotd']==VERIFY_ROBOTD_SHA256 else '.47'

    def legacy_terminal(self,m,prepared=None):
        # The published .47 and .48 vectors share these exact service/voice bytes.
        # Unmarked unfinished recovery remains with the original signed updater.
        if m['old_sha256'] not in self.legacy.SUPPORTED_SHA256:raise ValueError('unknown predecessor preimage')
        if 'restore_origin_sha256' in m or 'restore_updater_sha256' in m:
            self.owned_restore_origin(m)
            if m['phase']!='restored' or m['active'] or m['restore_pending'] or m['cleanup_pending']:return
            if self.base.has_exclusion() and not self.present(PREPARED):
                expected=[(self.legacy.ACTIVE,m['transaction_id'].encode()),
                    (self.legacy.RESTORE_PERMIT,m['transaction_id'].encode()),(self.legacy.GUARD,self.base.guard(m)),
                    *((path,self.base.controller_guard(m)) for path in self.base.controller_guard_paths())]
                if any(self.present(path) and self.read(path,65536)!=body for path,body in expected):
                    raise ValueError('predecessor restore cleanup ownership changed')
                return
        historical=bool(prepared and prepared['transaction_id']!=m['transaction_id'] and m['phase']=='restored'
            and self.read(STATE+'/'+m['transaction_id']+'/journal.json',65536)==self.read(JOURNAL,65536))
        if (m['active'] or m['restore_pending'] or m['cleanup_pending']
                or m['phase'] not in ('completed','restored') or not m['running_verified']
                or (not historical and (self.base.has_exclusion() or self.present(PREPARED)))):
            self.refuse(f'上一版 {self.predecessor_build(m)} 配套驱动尚未完成：请使用该版已签名软件包的更新器恢复后再更新',
                        'controller_recovery_required')
        if m['phase']=='completed':
            if not self.vector_current(m):raise ValueError('predecessor completed vector changed')
        elif any(self.snapshot(row['path'])!=row for row in m['records']):
            raise ValueError('predecessor restored vector changed')

    def owned_restore_origin(self,m):
        raw=self.read(STATE+'/'+m['transaction_id']+'/restore-origin.json',65536)
        origin=json.loads(raw)
        if (m.get('target_robotd') not in (ROBOTD_SHA256,LEGACY_ROBOTD_SHA256,VERIFY_ROBOTD_SHA256)
                or m.get('restore_updater_sha256')!=ROBOTD_SHA256 or m.get('restore_origin_sha256')!=sha(raw)
                or m['phase'] not in ('restoring','recovery_required','restored')
                or (m['phase']!='restored' and not m['active'])
                or any(origin.get(key)!=m.get(key) for key in RESTORE_IMMUTABLE)
                or origin.get('phase')!='completed' or origin.get('active') is not False
                or origin.get('restore_pending') is not False or origin.get('cleanup_pending') is not False
                or origin.get('running_verified') is not True
                or 'restore_origin_sha256' in origin or 'restore_updater_sha256' in origin):
            raise ValueError('untrusted predecessor restore origin')
        return origin

    def begin_restore(self,m):
        raw=self.read(JOURNAL,65536)
        if json.loads(raw)!=m:self.refuse('上一版配套驱动记录已改变','controller_tampered')
        directory=STATE+'/'+m['transaction_id'];path=directory+'/restore-origin.json'
        if self.present(path):
            if self.read(path,65536)!=raw:self.refuse('上一版恢复起点已改变','controller_tampered')
            self.base.sync_dir(directory)
        else:self.write(path,raw,0o600)
        self.save(m,'restoring',active=True,running_verified=False,
            restore_origin_sha256=sha(raw),restore_updater_sha256=ROBOTD_SHA256)

    def archive_legacy(self,m):
        raw=self.read(JOURNAL,65536)
        if json.loads(raw)!=m:self.refuse('上一版配套驱动记录已改变','controller_tampered')
        directory=STATE+'/'+m['transaction_id'];history=directory+'/journal.json'
        if self.present(history):
            if self.read(history,65536)!=raw:self.refuse('上一版配套驱动历史已改变','controller_tampered')
            self.base.sync_dir(directory)
        else:self.write(history,raw,0o600)

    def state(self,unit):
        value=self.unit(unit)
        if (value.get('LoadState'),value.get('ActiveState'),value.get('SubState'))==('not-found','inactive','dead'):return 'absent'
        if value.get('LoadState')=='loaded' and (value.get('ActiveState'),value.get('SubState')) in (
                ('active','running'),('inactive','dead'),('failed','failed')):return value['ActiveState']
        self.refuse('服务状态未知或仍在过渡：'+unit)

    def properties(self,unit):
        return self.h.show(self.op,unit,['User','Group','SupplementaryGroups','StateDirectory','RestrictAddressFamilies','FragmentPath','DropInPaths'])

    def configuration(self):
        self.base.config()
        rows=[]
        for unit,names in KNOWN_OVERRIDES.items():
            if self.state(unit)=='absent':
                self.refuse('缺少已审核 20261027 配套运行服务：'+unit)
            props=self.properties(unit);fragment=props.get('FragmentPath','')
            user={'padd':'padd','duck-echo':'radxa','microduck-studio':'radxa','mediad':'mediad'}[unit]
            if props.get('User')!=user:self.refuse('服务账户不是已审核安装：'+unit)
            if unit=='mediad' and set(props.get('RestrictAddressFamilies','').split()) not in (
                    {'AF_UNIX','AF_INET','AF_INET6'},{'AF_UNIX','AF_INET','AF_INET6','AF_NETLINK'}):
                self.refuse('媒体地址族策略不是已审核前驱，不能覆盖自定义沙箱')
            if unit=='mediad':
                if sha(self.read(MEDIAD))!=MEDIAD_SHA256:self.refuse('媒体磁盘程序不是已审核前驱')
                rows.append(self.snapshot(MEDIAD))
            if fragment not in tuple(parent+'/'+unit+'.service' for parent in ('/etc/systemd/system','/lib/systemd/system','/usr/lib/systemd/system')):
                self.refuse('不支持自定义服务安装路径：'+unit)
            rows.append(self.snapshot(fragment))
            for path in props.get('DropInPaths','').split():
                prefix='/etc/systemd/system/'+unit+'.service.d/'
                if not path.startswith(prefix) or path[len(prefix):] not in names|{DROPIN,'zzzzzzz-microduck-native-update.conf'}:
                    self.refuse('存在未审核的服务覆盖：'+unit)
                if not path.endswith('/'+DROPIN) and not path.endswith('/zzzzzzz-microduck-native-update.conf'):
                    if path.endswith('/50-microduck-camera-network.conf') and self.read(path)!=UNITS['/etc/systemd/system/mediad.service.d/'+DROPIN]:
                        self.refuse('媒体网络配置不是已审核兼容修复')
                    rows.append(self.snapshot(path))
        return rows

    def unchanged(self,m):
        self.base.unchanged(m)
        for row in m.get('service_files',[]):
            if self.snapshot(row['path'])!=row:self.refuse('服务安装文件已被外部改变，保留现场','controller_user_files_changed')

    def gid(self):
        text=self.read('/etc/group',65536).decode()
        rows=[line.split(':') for line in text.splitlines() if line.startswith('robot:')]
        if len(rows)!=1 or not rows[0][2].isdigit():self.refuse('缺少固定 robot 组')
        return int(rows[0][2])

    def uid(self,name):
        rows=[line.split(':') for line in self.read('/etc/passwd',65536).decode().splitlines() if line.startswith(name+':')]
        if len(rows)!=1 or len(rows[0])!=7 or not rows[0][2].isdigit():self.refuse('缺少固定服务账户：'+name)
        return int(rows[0][2])

    def padd_data(self,path,mode,limit):
        """Fixed padd-owned data is not a root-owned executable/config input."""
        assert path in (PROFILE,RUNTIME)
        parts=path.strip('/').split('/')
        try:
            for index in range(1,len(parts)+1):
                info=os.lstat(self.ctx.p('/'+'/'.join(parts[:index])))
                is_file=index==len(parts)
                expected=self.uid('padd') if index>=len(parts)-1 else 0
                if ((not stat.S_ISREG(info.st_mode) if is_file else not stat.S_ISDIR(info.st_mode))
                        or (self.ctx.check_owner and (info.st_uid!=expected or info.st_mode&0o022))
                        or (is_file and os.name=='posix' and stat.S_IMODE(info.st_mode)!=mode)):
                    self.refuse('手柄数据属主 / 权限不安全','controller_tampered')
            if info.st_size>limit:self.refuse('手柄数据超出大小限制')
            fd=os.open(self.ctx.p(path),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
            with os.fdopen(fd,'rb') as source:
                if (os.fstat(source.fileno()).st_ino,os.fstat(source.fileno()).st_dev)!=(info.st_ino,info.st_dev):self.refuse('手柄数据读取期间改变')
                data=source.read(limit+1)
            if len(data)>limit:self.refuse('手柄数据超出大小限制')
            if self.ctx.check_owner and path==RUNTIME and info.st_gid!=self.gid():self.refuse('手柄运行时 robot 组无效')
            return data
        except FileNotFoundError:return None

    def profile(self):
        data=self.padd_data(PROFILE,0o600,262144)
        if data is not None:
            value=json.loads(data)
            if (type(value.get('schema')) is not int or value['schema']!=1 or not isinstance(value.get('profiles'),dict)
                    or len(value['profiles'])>32):self.refuse('现有手柄映射配置无效，保留配置并停止升级')
        return sha(data) if data is not None else None

    def runtime(self,pid):
        data=self.padd_data(RUNTIME,0o640,65536)
        if data is None:return None
        value=json.loads(data)
        if (type(value.get('version')) is not int or value['version']!=1 or type(value.get('pid')) is not int
                or value['pid']!=pid or type(value.get('output_blocked')) is not bool):self.refuse('映射运行时不属于已验证的新 padd')
        return {'pid':pid,'sha256':sha(data)}

    def capture_runtime(self,m):
        if self.state('padd')=='active' and sha(self.read(PADD))==PADD_SHA256:
            pad=self.process('padd',PADD_SHA256,m['padd_args'])
            owned=self.runtime(int(pad['unit']['MainPID']))
            if owned is not None:self.save(m,m['phase'],runtime_owned=owned)

    def cleanup_runtime(self,m):
        if not self.present(RUNTIME):return
        owned=m.get('runtime_owned')
        if owned is None or self.runtime(owned['pid'])!=owned:self.refuse('保留非本事务产生的映射运行时元数据')
        self.h.remove(self.ctx,RUNTIME);self.base.sync_dir('/run/padd')

    def process(self,unit,expected=None,args=None,groups=False):
        installed_path=PADD
        if unit=='mediad':expected=MEDIAD_SHA256;installed_path=MEDIAD
        before=self.unit(unit)
        if self.state(unit)!='active':self.refuse('服务未稳定运行：'+unit)
        pid=before.get('MainPID','')
        if not pid.isdigit() or int(pid)<=0 or not before.get('InvocationID'):self.refuse('服务进程代次未知：'+unit)
        base='/proc/'+pid
        with open(self.ctx.p(base+'/stat'),encoding='utf-8') as source:start=source.read().rsplit(')',1)[1].split()[19]
        with open(self.ctx.p(base+'/cmdline'),'rb') as source:actual=source.read(8193).rstrip(b'\0').split(b'\0')
        if args is not None and actual!=[s.encode() for s in args]:self.refuse('实际进程参数已改变：'+unit)
        if unit=='mediad' and (not actual or actual[0]!=MEDIAD.encode()):self.refuse('实际媒体程序路径不属于已审核安装')
        if unit=='duck-echo':
            allowed=('/opt/microduck/software/current/duck-echo/duck_echo.py',self.release+'/duck-echo/duck_echo.py')
            if (len(actual)<3 or actual[0]!=b'/usr/bin/python3' or actual[1] not in [a.encode() for a in allowed]
                    or actual[2]!=b'daemon' or b'--control-socket' not in actual
                    or actual[actual.index(b'--control-socket')+1:]!=[b'/run/duck-echo/control.sock']):
                self.refuse('实际语音进程未运行受管理的映射兼容入口')
        if unit=='microduck-studio':
            wanted=[b'/usr/bin/python3',b'-I',b'run.py',b'--listen',b'0.0.0.0:8000',b'--state-dir',b'/var/lib/microduck-studio',b'--log-dir',b'/var/log/microduck-studio']
            if actual!=wanted or os.path.realpath(self.ctx.p(base+'/cwd'))!=os.path.realpath(self.ctx.p(self.release+'/studio')):
                self.refuse('实际网页进程未运行受管理入口或状态目录')
        if expected is not None:
            with open(self.ctx.p(base+'/exe'),'rb') as source:
                info=os.fstat(source.fileno());digest=hashlib.file_digest(source,'sha256').hexdigest()
            installed=os.stat(self.ctx.p(installed_path),follow_symlinks=False)
            if (info.st_dev,info.st_ino)!=(installed.st_dev,installed.st_ino) or digest!=expected or sha(self.read(installed_path))!=expected:
                self.refuse('实际 native inode / 摘要与磁盘不一致：'+unit)
        if groups:
            with open(self.ctx.p(base+'/status'),encoding='utf-8') as source:status=dict(line.split(':',1) for line in source if ':' in line)
            if self.gid() not in [int(value) for value in status.get('Groups','').split()]:self.refuse('实际进程尚未取得 robot 组：'+unit)
        with open(self.ctx.p(base+'/status'),encoding='utf-8') as source:status=dict(line.split(':',1) for line in source if ':' in line)
        user={'padd':'padd','mediad':'mediad','gamesir-xboxd':'root','duck-echo':'radxa','microduck-studio':'radxa'}[unit]
        if any(int(value)!=self.uid(user) for value in status.get('Uid','').split()) or len(status.get('Uid','').split())!=4:
            self.refuse('实际服务账户不匹配：'+unit)
        if self.unit(unit)!=before:self.refuse('验证期间服务进程被替换：'+unit)
        return {'unit':before,'start_ticks':start,'boot_id':self.ctx.boot_id()}

    def pad_arguments(self):
        pid=self.unit('padd').get('MainPID','')
        if not pid.isdigit() or int(pid)<=0:self.refuse('原 padd 没有运行')
        with open(self.ctx.p('/proc/'+pid+'/cmdline'),'rb') as source:
            args=[s.decode() for s in source.read(8193).rstrip(b'\0').split(b'\0')]
        self.validate_pad_arguments(args)
        return args

    def validate_pad_arguments(self,args):
        if (not isinstance(args,list) or any(not isinstance(a,str) for a in args) or not args or args[0]!=PADD
                or len(args)>15 or len(args)%2!=1):self.refuse('原 padd 参数不受支持')
        flags=dict(zip(args[1::2],args[2::2]))
        if (len(flags)*2!=len(args)-1 or set(flags)-{'--socket','--config','--max-head','--input-settings'}
                or flags.get('--socket')!='/run/robotd.sock' or flags.get('--config')!=self.legacy.CONFIG
                or flags.get('--input-settings','/etc/robot/feetech-ft5/pad-settings.json')!='/etc/robot/feetech-ft5/pad-settings.json'):
            self.refuse('原 padd 参数不属于固定安装')
        if '--max-head' in flags:
            try:value=float(flags['--max-head'])
            except ValueError:self.refuse('手柄幅度参数无效')
            if not math.isfinite(value) or not 0<value<=10:self.refuse('手柄幅度参数无效')

    def gate(self):
        # Relax changes Bringup::Ready to Limp and therefore clears homed in
        # real robotd. Retain the existing two-snapshot, all-off stationary gate;
        # do not invent an unreachable homed=True + torque-off requirement.
        # Factory/startup transactions remain the unchanged v1 path.
        bus=self.ctx.rpc('robot.busStatus')
        if bus.get('mode')!='motion' or bus.get('phase')!='control':
            self.refuse('先完成标定并进入普通运动控制，关闭策略并卸力，再安装配套驱动')
        if self.base.gate()!='motion':self.refuse('主程序未稳定停在运动控制模式')

    def unpublished_readiness(self,m):
        try:
            studio=self.legacy.json_socket(self.ctx.p('/run/microduck-studio/update.sock'),
                {'op':'native-recovery-readiness','transaction_id':m['transaction_id']})
            if studio.get('ready') is not True:self.refuse('网页仍有活动或不支持本事务的准备态恢复检查')
            self.voice_health()
        except (OSError,ValueError,TypeError,AttributeError):self.refuse('无法核验准备态恢复的网页 / 语音空闲')

    def bridge(self,m):
        if self.present(self.legacy.ACTIVE) and self.read(self.legacy.ACTIVE,128)!=m['transaction_id'].encode():
            self.refuse('旧版可见保护不属于本事务')
        self.directory(self.legacy.STATE)
        # Persist the exclusion understood by old helpers BEFORE changing any
        # service or owned component. The v2 journal never replaces the v1 one.
        self.write(self.legacy.ACTIVE,m['transaction_id'].encode(),0o644)
        self.base.exclude_controller_starts(m)
        for path,body in [(self.legacy.GUARD,self.base.guard(m))]:
            if self.present(path) and self.read(path,65536)!=body:self.refuse('主程序保护不属于本事务')
            self.directory(os.path.dirname(path));self.write(path,body,0o644)
        if self.ctx.run(['systemctl','daemon-reload'])[0]:self.refuse('保护配置未生效')

    def stop_services(self,m):
        errors=[]
        for unit in SERVICES:
            try:
                rc=self.ctx.run(['systemctl','stop',unit],timeout=20)[0]
                state=self.state(unit)
                if state=='active' or (rc and state!='absent'):errors.append(unit)
            except Exception:errors.append(unit)
        if errors:self.refuse('停止或核验控制服务失败：'+'、'.join(errors))

    def stop_robot(self):
        if self.ctx.run(['systemctl','stop','robotd'],timeout=20)[0] or self.unit('robotd').get('ActiveState') not in ('inactive','failed'):
            self.refuse('主程序未停止')

    def owned(self,m):
        self.unchanged(m)
        self.protected_unchanged(m)
        for row in m['records']:
            current=self.snapshot(row['path'])
            wanted=m['targets'][row['path']]
            old_ok=current==row
            new_ok=(current['existed'] and current['sha256']==wanted['sha256'] and (os.name!='posix' or current['mode']==wanted['mode'])
                    and (not self.ctx.check_owner or current['uid']==0))
            if not old_ok and not new_ok:self.refuse('升级组件已被外部改变：'+row['path'],'controller_tampered')

    def protected_unchanged(self,m):
        current=json.loads(self.read(self.legacy.PROTECTED,65536)) if self.present(self.legacy.PROTECTED) else None
        lost=current is None and self.ctx.boot_id()!=m.get('baseline_boot_id',m['original_process']['boot_id'])
        allowed=[m.get('protected'),m.get('protected_next')]
        if m.get('target_robotd') in (ROBOTD_SHA256,LEGACY_ROBOTD_SHA256,VERIFY_ROBOTD_SHA256) and m.get('restore_updater_sha256')==ROBOTD_SHA256:
            allowed.append(self.owned_restore_origin(m).get('protected_next'))
        if current not in allowed and not lost:
            self.refuse('软件升级器的 native 进程基线被外部改变，保留现场')

    def install_bytes(self,path,data,mode,uid=0,gid=0):
        self.directory(os.path.dirname(path))
        if path in (COMPAT,VOICE_GUARD):self.public_parents(path)
        self.write(path,data,mode)
        if os.name=='posix' and self.ctx.check_owner:
            os.chown(self.ctx.p(path),uid,gid,follow_symlinks=False)
            descriptor=os.open(self.ctx.p(path),os.O_RDONLY|os.O_NOFOLLOW)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
            self.base.sync_dir(os.path.dirname(path))

    def robot_verify(self,m,expected,mode):
        if self.ctx.run(['systemctl','start','robotd'],timeout=20)[0]:self.refuse('主程序启动失败')
        deadline=self.ctx.now()+40
        while True:
            try:
                identity=self.base.process(expected,self.legacy.ARGS+(['--commissioning'] if mode=='commissioning' else []))
                ipc=self.legacy.IPC_API if expected==ROBOTD_SHA256 else self.legacy.PREDECESSOR_IPC_API.get(expected)
                if self.ctx.rpc('hello').get('api_version')!=ipc:self.refuse('主程序 IPC 不匹配')
                if expected==ROBOTD_SHA256:
                    rc,out,_=self.ctx.run([ROBOTD,'deployment-info'])
                    if rc or json.loads(out)!={'build':ROBOTD_BUILD,'model_api':2,'official_version':'0.15.1'}:self.refuse('主程序构建元数据不匹配')
                if self.base.gate()!=mode:self.refuse('主程序模式不匹配')
                self.unchanged(m)
                if self.base.process(expected,self.legacy.ARGS+(['--commissioning'] if mode=='commissioning' else []))!=identity:
                    self.refuse('核验期间主程序进程代次改变')
                return identity
            except (self.h.Stop,self.h.RpcError,OSError,ValueError):
                if self.ctx.now()>=deadline:raise
                self.ctx.sleep(.5)

    def normal_robot(self,m,expected):
        self.robot_verify(m,expected,'commissioning')
        self.stop_robot()
        if not self.present(self.legacy.GUARD) or self.read(self.legacy.GUARD)!=self.base.guard(m):self.refuse('主程序保护文件已改变')
        self.h.remove(self.ctx,self.legacy.GUARD);self.base.sync_dir(os.path.dirname(self.legacy.GUARD))
        if self.ctx.run(['systemctl','daemon-reload'])[0] or self.base.args()!=self.legacy.ARGS:self.refuse('主程序启动命令未恢复')
        return self.robot_verify(m,expected,'motion')

    def vector_current(self,m):
        return all(self.present(path) and sha(self.read(path))==row['sha256']
                   and (os.name!='posix' or stat.S_IMODE(os.lstat(self.ctx.p(path)).st_mode)==row['mode']) for path,row in m['targets'].items())

    def factory_current(self):
        if (self.present(JOURNAL) or self.present(PREPARED) or self.base.has_exclusion()
                or not self.vector_current({'targets':self.targets()})):return False
        self.base.process(ROBOTD_SHA256,self.base.args())
        self.target_health({'services':{'mediad':self.state('mediad')},'padd_args':self.pad_arguments()})
        return True

    def target_health(self,m):
        if sha(self.read(MEDIAD))!=MEDIAD_SHA256:self.refuse('媒体磁盘程序已改变')
        pad=self.process('padd',PADD_SHA256,m['padd_args'],groups=True)
        props=self.properties('padd')
        if props.get('StateDirectory')!='padd':self.refuse('padd 状态目录未生效')
        for unit in ('microduck-studio','duck-echo'):
            self.process(unit,groups=True)
            if 'robot' not in self.properties(unit).get('SupplementaryGroups','').split():self.refuse('robot 附加组未生效：'+unit)
        if m['services']['mediad']=='active':
            self.process('mediad')
            if set(self.properties('mediad').get('RestrictAddressFamilies','').split())!={'AF_UNIX','AF_INET','AF_INET6','AF_NETLINK'}:
                self.refuse('媒体服务的地址族沙箱未生效')
        # Padd itself is the authoritative capability source. Read its bounded
        # public state, without a token, and bind it to the verified live PID.
        owned=self.runtime(int(pad['unit']['MainPID']))
        if owned is None:self.refuse('padd 实际映射接口尚未确认')
        if self.ctx.check_owner:
            info=os.lstat(self.ctx.p('/run/padd/mapping.sock'))
            if (not stat.S_ISSOCK(info.st_mode) or info.st_uid!=self.uid('padd') or info.st_gid!=self.gid()
                    or stat.S_IMODE(info.st_mode)!=0o660):self.refuse('实际映射接口属主 / 权限无效')
        capability=mapping_status(self.ctx.p('/run/padd/mapping.sock'))
        if (not isinstance(capability,dict) or capability.get('ok') is not True or type(capability.get('version')) is not int
                or capability['version']!=1 or not isinstance(capability.get('status'),dict)
                or capability['status'].get('output_blocked') is not False):self.refuse('实际手柄映射能力未就绪或仍处于校准暂停')
        self.profile()
        return pad

    def start_services(self,m,target,exclude=()):
        self.write(self.legacy.RESTORE_PERMIT,m['transaction_id'].encode(),0o600)
        deadline=self.ctx.now()+70
        def wait(check):
            while True:
                try:return check()
                except (self.h.Stop,self.h.RpcError,OSError,ValueError,TypeError,KeyError):
                    if self.ctx.now()>=deadline:raise
                    self.ctx.sleep(.5)
        for unit,state in m['services'].items():
            if state=='active' and unit not in exclude:
                if self.ctx.run(['systemctl','start',unit],timeout=20)[0]:self.refuse('恢复服务失败：'+unit)
                current=wait(lambda:self.process(unit))
                if current['unit']==m['original_services'].get(unit):self.refuse('服务没有创建新进程：'+unit)
        def healthy():
            pad=self.target_health(m) if target else self.process('padd',m['records'][1]['sha256'],m['padd_args'])
            studio=self.legacy.json_socket(self.ctx.p('/run/microduck-studio/update.sock'),{'op':'readiness'})
            if type(studio.get('ready')) is not bool:self.refuse('新网页内部状态接口尚未就绪')
            if 'duck-echo' not in exclude:
                self.voice_health()
            return pad
        return wait(healthy)

    def voice_health(self):
        voice=self.legacy.json_socket(self.ctx.p('/run/duck-echo/control.sock'),{'method':'status'})
        if (voice.get('ok') is not True or (voice.get('status') or {}).get('listening') is not False
                or (voice.get('status') or {}).get('music') is not False):self.refuse('语音实际状态接口尚未就绪或活动未关闭')

    def complete(self,m,phase,robot,pad):
        self.unchanged(m)
        self.protected_unchanged(m)
        self.gate()
        if self.base.process(robot['sha256'],self.legacy.ARGS)!=robot:self.refuse('提交前主程序进程代次改变')
        if phase=='completed':self.capture_runtime(m)
        baseline={'robotd':robot['unit'],'padd':pad['unit']}
        self.save(m,phase,active=True,restore_pending=True,cleanup_pending=True,protected_next=baseline,baseline_boot_id=self.ctx.boot_id())
        self.write(self.legacy.PROTECTED,json.dumps(baseline).encode(),0o600)
        # Commit is durable before cleanup starts. Old helpers must continue to
        # see their persistent bridge while any v2 pending flag remains true.
        self.save(m,phase,active=False,restore_pending=False,cleanup_pending=False,running_verified=True)
        self.base.remove_controller_exclusion(m)

    def protect(self,m):
        errors=[]
        for label,function in [('runtime',lambda:self.capture_runtime(m)),
                               ('journal',lambda:self.save(m,'recovery_required',active=True,running_verified=False)),
                               ('bridge',lambda:self.bridge(m)),('robot',self.stop_robot),('controllers',lambda:self.stop_services(m))]:
            try:function()
            except Exception:errors.append(label)
        return errors

    def restore(self,m):
        if self.protect(m):self.refuse('保护或停止失败，保留恢复状态','controller_recovery_required')
        self.owned(m);self.save(m,'restoring')
        # Keep the release-external legacy guard until the previous native
        # vector is actually running. A reboot cannot open old voice halfway.
        last={VOICE_GUARD,COMPAT,'/etc/systemd/system/duck-echo.service.d/'+DROPIN}
        def restore_row(index,row):
            if row['existed']:
                data=self.read(STATE+'/'+m['transaction_id']+'/component-'+str(index))
                self.install_bytes(row['path'],data,row['mode'],row['uid'],row['gid'])
            elif self.present(row['path']):
                self.h.remove(self.ctx,row['path']);self.base.sync_dir(os.path.dirname(row['path']))
        profile=self.profile()
        for index,row in enumerate(m['records']):
            if row['path'] not in last:restore_row(index,row)
        if self.ctx.run(['systemctl','daemon-reload'])[0]:self.refuse('恢复服务配置失败')
        robot=self.normal_robot(m,m['records'][0]['sha256'])
        pad=self.start_services(m,False,exclude=('duck-echo',))
        self.cleanup_runtime(m)
        if self.profile()!=profile:self.refuse('恢复期间手柄持久配置改变，未覆盖配置')
        for index,row in enumerate(m['records']):
            if row['path'] in last:restore_row(index,row)
        if self.ctx.run(['systemctl','daemon-reload'])[0]:self.refuse('恢复旧语音配置失败')
        if self.ctx.run(['systemctl','start','duck-echo'],timeout=20)[0]:self.refuse('恢复旧语音服务失败')
        deadline=self.ctx.now()+40
        while True:
            try:self.process('duck-echo');self.voice_health();break
            except (self.h.Stop,OSError,ValueError,TypeError):
                if self.ctx.now()>=deadline:raise
                self.ctx.sleep(.5)
        if any(self.snapshot(row['path'])!=row for row in m['records']):self.refuse('旧组件向量恢复回读不一致')
        self.complete(m,'restored',robot,pad)

    def prepare(self,payload,previous_bundle=None):
        # Never reinterpret a pending robotd-only transaction as a new bundle.
        previous=self.base.load()
        if previous and (previous['active'] or previous.get('restore_pending') or previous.get('cleanup_pending')):
            self.refuse('先用原签名软件包恢复单独 robotd 更新')
        if self.base.has_exclusion():self.refuse('存在未归属的旧更新保护')
        for path in (COMPAT,VOICE_GUARD,JOURNAL):self.public_parents(path)
        self.base.readiness();self.base.config();self.base.resources();self.gate()
        args=self.base.args()
        if args!=self.legacy.ARGS:self.refuse('伴随升级仅支持已完成标定的运动模式')
        robot_hash=sha(self.read(ROBOTD));pad_hash=sha(self.read(PADD))
        if robot_hash not in (*self.legacy.SUPPORTED_SHA256,ROBOTD_SHA256) or pad_hash not in SUPPORTED_PADD_SHA256:
            self.refuse('当前 native 组件不属于已审核前驱版本')
        robot=self.base.process(robot_hash,args);pad_args=self.pad_arguments();pad=self.process('padd',pad_hash,pad_args)
        service_files=self.configuration()
        for path in (*UNITS,VOICE_GUARD,COMPAT):
            if self.present(path):self.refuse('受控配置路径已被占用，不能覆盖未知文件')
        states={unit:self.state(unit) for unit in SERVICES}
        if any(states[unit]!='active' for unit in ('padd','duck-echo','microduck-studio')):self.refuse('配套控制服务必须稳定运行')
        for unit,state in states.items():
            if state=='active':self.process(unit)
        profile=self.profile()
        protected=json.loads(self.read(self.legacy.PROTECTED,65536)) if self.present(self.legacy.PROTECTED) else None
        if protected is not None and protected!={'robotd':robot['unit'],'padd':pad['unit']}:self.refuse('软件升级服务记录的 native 进程已改变')
        self.directory(STATE);ident=uuid.uuid4().hex;self.directory(STATE+'/'+ident,0o700)
        records=[]
        for index,path in enumerate(self.paths()):
            row=self.snapshot(path);records.append(row)
            if row['existed']:self.write(STATE+'/'+ident+'/component-'+str(index),self.read(path),0o600)
        files=[]
        for path in (*self.legacy.USER_FILES,PAD_SETTINGS):
            row=self.snapshot(path);files.append({key:row[key] for key in ('path','existed','sha256')})
        m={'schema':SCHEMA,'bundle_schema':PAYLOAD_SCHEMA,'active':True,'transaction_id':ident,'mode':'motion',
           'restore_pending':False,'cleanup_pending':False,'target_robotd':ROBOTD_SHA256,'target_padd':PADD_SHA256,
           'old_sha256':robot_hash,'target_sha256':ROBOTD_SHA256,'records':records,'files':files,'service_files':service_files,
           'targets':self.targets(),
           'services':states,'controllers':{u:states[u]=='active' for u in self.legacy.CONTROLLERS},
           'original_services':{u:self.unit(u) for u,s in states.items() if s=='active'},'original_process':robot,
           'padd_args':pad_args,'profile_sha256':profile,'protected':protected,'running_verified':False}
        if previous_bundle and previous_bundle['target_robotd']!=ROBOTD_SHA256:self.archive_legacy(previous_bundle)
        self.save(m,'prepared');return m

    def status(self):
        m=self.load();active=bool(m and (m['active'] or m.get('restore_pending') or m.get('cleanup_pending'))) or self.base.has_exclusion() or self.present(PREPARED)
        try:
            robot_hash=sha(self.read(ROBOTD));pad_hash=sha(self.read(PADD))
            self.base.process(robot_hash,self.base.args());pad_args=self.pad_arguments();self.process('padd',pad_hash,pad_args)
            supported=robot_hash in (*self.legacy.SUPPORTED_SHA256,ROBOTD_SHA256) and pad_hash in (*SUPPORTED_PADD_SHA256,PADD_SHA256)
            restorable=bool(m and m['phase']=='completed' and not active and self.vector_current(m))
            if restorable:self.target_health(m)
            current=bool(restorable and m['target_robotd']==ROBOTD_SHA256) or (m is None and self.factory_current())
        except (self.h.Stop,OSError,ValueError,TypeError):supported=False;current=restorable=False;robot_hash=pad_hash=None
        try:self.payload();available=True
        except (self.h.Stop,OSError,ValueError,TypeError):available=False
        detail={key:m.get(key) for key in PUBLIC if m and key in m}
        detail.update(active=active,phase=m['phase'] if m else 'idle',bundle_schema=PAYLOAD_SCHEMA,bundle_current=current,
            available=available,running_verified=supported,restorable=restorable,
            installed_sha256=robot_hash,target_sha256=ROBOTD_SHA256,
            components={'robotd':{'installed_sha256':robot_hash,'target_sha256':ROBOTD_SHA256},
                        'padd':{'installed_sha256':pad_hash,'target_sha256':PADD_SHA256}})
        if m and m['target_robotd']!=ROBOTD_SHA256 and m['phase']=='completed':detail['reason']=PREDECESSOR_PROCEDURE.format(self.predecessor_build(m))
        return self.op.finish(0,'controller_status',detail=detail)

    def run(self,action):
        if action=='status':return self.status()
        self.op.take_lock('配套驱动升级');locks=[]
        try:
            for path in (self.h.SERVICE_LOCK,self.legacy.MOTION_LOCK):
                if path==self.h.SERVICE_LOCK and not self.present(path):self.directory(os.path.dirname(path))
                else:self.base.read_lock(path)
                descriptor=self.ctx.lock(path)
                if descriptor is None:raise self.h.Stop(4,'busy','其他控制或升级仍在进行')
                locks.append(descriptor)
                self.base.read_lock(path)
            if self.present(self.legacy.SOFTWARE_MARKER):self.refuse('软件更新尚未结束')
            self.h.imu_redo_conflict(self.op)
            if self.present(self.h.FLAG) or self.present(self.h.MARKER):self.refuse('舵机会话尚未结束')
            m=self.load()
            if action in ('restore','recover'):
                if not m:
                    if self.base.has_exclusion():self.refuse('保护存在但伴随记录缺失')
                    self.refuse('没有可以恢复的伴随升级记录')
                if action=='recover' and not m['active'] and not m.get('restore_pending') and not m.get('cleanup_pending') and not self.base.has_exclusion() and not self.present(PREPARED):
                    return self.status()
                unpublished=self.present(PREPARED) and not self.present(self.legacy.ACTIVE)
                if unpublished:
                    # No target write was authorized before ACTIVE publication.
                    # Validate all fixed preimages before creating any exclusion.
                    self.owned(m)
                    if any(self.snapshot(row['path'])!=row for row in m['records']):self.refuse('未发布准备记录的旧组件已改变')
                    self.unpublished_readiness(m);self.gate()
                if action=='restore':
                    self.base.readiness();self.gate();self.unchanged(m)
                    if m['phase']=='completed':self.begin_restore(m)
                try:self.restore(m)
                except Exception as error:
                    self.protect(m);self.save(m,'recovery_required',active=True,reason=str(error)[:300]);return self.op.finish(5,'controller_recovery_required',detail={k:m.get(k) for k in PUBLIC if k in m})
                return self.op.finish(0,'controller_restored',detail={k:m.get(k) for k in PUBLIC if k in m})
            if m:
                if m['active'] or m.get('restore_pending') or m.get('cleanup_pending'):self.refuse('先恢复未完成的伴随升级')
                if m['phase']=='completed':
                    self.owned(m)
                    if not self.vector_current(m):self.refuse('已完成的组件向量已改变，不能当作重复安装成功')
                    self.target_health(m)
                    if m['target_robotd']!=ROBOTD_SHA256:self.refuse(PREDECESSOR_PROCEDURE.format(self.predecessor_build(m)),'controller_predecessor_restore_required')
                    return self.status()
            if m is None and self.factory_current():return self.status()
            payload=self.payload();m=self.prepare(payload,m)
            try:
                self.save(m,'guarding');self.bridge(m);self.stop_services(m);self.gate()
                identity=self.base.process(m['old_sha256'],self.legacy.ARGS)
                original=m['original_process']
                if (any(identity[key]!=original[key] for key in ('start_ticks','boot_id','sha256'))
                        or any(identity['unit'].get(k)!=original['unit'].get(k) for k in ('MainPID','InvocationID','ExecMainStartTimestampMonotonic'))):
                    self.refuse('替换前主程序的实际进程代次已改变')
                if self.profile()!=m['profile_sha256']:self.refuse('升级期间手柄配置改变，未覆盖配置')
                self.owned(m);self.stop_robot();self.save(m,'replacing')
                for path,data in payload.items():self.install_bytes(path,data,m['targets'][path]['mode'])
                if self.ctx.run(['systemctl','daemon-reload'])[0]:self.refuse('配套服务配置未加载')
                if not self.vector_current(m):self.refuse('组件向量回读不一致')
                self.save(m,'verifying');robot=self.normal_robot(m,ROBOTD_SHA256);pad=self.start_services(m,True)
                if self.profile()!=m['profile_sha256']:self.refuse('升级期间手柄配置改变，未覆盖配置')
                self.complete(m,'completed',robot,pad)
                return self.op.finish(0,'controller_installed',detail={k:m.get(k) for k in PUBLIC if k in m})
            except Exception as error:
                try:self.restore(m)
                except Exception as restore_error:
                    self.protect(m);self.save(m,'recovery_required',active=True,reason=str(restore_error)[:300])
                return self.op.finish(5,'controller_failed',reason=str(error)[:300],detail={k:m.get(k) for k in PUBLIC if k in m})
        finally:
            for descriptor in reversed(locks):self.ctx.unlock(descriptor)
            self.op.release_lock()


def handle(helper,op,args,release,legacy):
    if (set(args)-{'by','action','confirmed'} or args.get('by')!='Studio' or args.get('action') not in ('status','install','recover','restore')
            or (args['action']!='status' and args.get('confirmed') is not True)
            or ('confirmed' in args and type(args['confirmed']) is not bool)):
        raise helper.Stop(2,'bad_args','伴随升级只接受 Studio 的固定动作及明确确认')
    op.by='Studio'
    return BundleTransaction(helper,op,release,legacy).run(args['action'])
