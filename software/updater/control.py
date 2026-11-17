"""Independent, bounded update coordinator. updaterd alone verifies and swaps releases."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import struct
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from package import MAX_OUTER, unpack_outer, valid_version

REPOSITORY='fanhao375/microduck-replica'
PREFIX='microduck-software-v'
CATALOG=f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100'
MUTATIONS={'validate','install','rollback'}
TERMINAL={'completed','rolled_back','failed','waiting_validation','interrupted'}

class UpdateError(Exception):
    def __init__(self,code,message): super().__init__(message); self.code=code

def atomic_json(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.state-',dir=path.parent)
    try:
        os.chmod(name,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,allow_nan=False); stream.flush(); os.fsync(stream.fileno())
        os.replace(name,path)
        if os.name=='posix':
            directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)

def read_json(path,default=None):
    try:
        if Path(path).stat().st_size>2*1024*1024: return default
        return json.loads(Path(path).read_bytes())
    except (OSError,ValueError): return default

def json_socket(path,body,timeout=3,limit=1024*1024):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout); connection.connect(str(path))
        connection.sendall(json.dumps(body,separators=(',',':')).encode()+b'\n')
        with connection.makefile('rb') as reader:
            raw=reader.readline(limit+1)
            if len(raw)>limit or not raw.endswith(b'\n'): raise UpdateError('protocol','服务回包超限或中断')
            return json.loads(raw)

class Engine:
    def __init__(self,path='/run/microduck-updater/engine.sock'): self.path=path
    def call(self,method,params=None,progress=None):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(180 if method in {'update.apply','update.rollback'} else 5)
            connection.connect(self.path)
            connection.sendall(json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params or {}},separators=(',',':')).encode()+b'\n')
            with connection.makefile('rb') as reader:
                while True:
                    raw=reader.readline(1024*1024+1)
                    if len(raw)>1024*1024 or not raw.endswith(b'\n'): raise UpdateError('engine','升级引擎连接中断')
                    message=json.loads(raw)
                    if message.get('id')==1:
                        if 'error' in message: raise UpdateError('engine',str(message['error'].get('message','升级引擎拒绝请求'))[:500])
                        return message['result']
                    if progress and isinstance(message.get('params'),dict): progress(message['params'])

def numeric(value): return type(value) in (int,float) and math.isfinite(value)

def check_robot(bus,health,previous):
    if not isinstance(bus,dict) or not isinstance(previous,dict): raise UpdateError('preflight','无法确认主程序状态')
    if (any(bus.get(key)!=value or previous.get(key)!=value for key,value in [('mode','motion'),('phase','control')])
        or any(bus.get(key) is not value or previous.get(key) is not value for key,value in
            [('homed',True),('policy_enabled',False),('fresh_sample',True),('all_joints_fresh',True)])):
        raise UpdateError('preflight','请回到运动控制状态、完成 HOME 并关闭策略，等待关节反馈恢复')
    rows=bus.get('native',{}).get('joints',[]); old=previous.get('native',{}).get('joints',[])
    if len(rows)!=15 or len(old)!=15: raise UpdateError('preflight','缺少十五个关节的实时反馈')
    old={row.get('id'):row for row in old}
    if len(old)!=15 or len({row.get('id') for row in rows})!=15: raise UpdateError('preflight','关节反馈不完整')
    for row in rows:
        before=old.get(row.get('id'),{})
        for sample in (row,before):
            if (sample.get('feedback_fresh') is not True or not numeric(sample.get('sample_age_ms')) or sample['sample_age_ms']>250
                or not numeric(sample.get('velocity_rad_s')) or abs(sample['velocity_rad_s'])>.08
                or not numeric(sample.get('position_rad'))): raise UpdateError('preflight','关节尚未停稳或反馈过期')
        if abs(row['position_rad']-before['position_rad'])>.02: raise UpdateError('preflight','检测到关节实际运动，请等待停稳')
    # 不拦电量（用户 11-18）：边充电边装时读到的是带载电压，会被误拦；软件升级只重启中控和语音，不动主程序

def preflight(config):
    # The root marker is installed BEFORE this read: Studio refuses new commands
    # while its existing command set is inspected over its private local socket.
    studio=json_socket(config['studio_socket'],{'op':'readiness'})
    if studio.get('ready') is not True: raise UpdateError('preflight',studio.get('error','网页控制会话尚未空闲'))
    audio=json_socket(config['audio_socket'],{'method':'status'})
    status=audio.get('status',{})
    if audio.get('ok') is not True or status.get('listening') is not False or status.get('music') is not False:
        raise UpdateError('preflight','请关闭语音监听和音乐律动后重试')
    robot=Engine(config['robot_socket'])
    previous=robot.call('robot.busStatus'); time.sleep(.3)
    bus=robot.call('robot.busStatus'); health=robot.call('robot.health')
    check_robot(bus,health,previous)
    if shutil.disk_usage(config['state_dir']).free<512*1024*1024:
        raise UpdateError('preflight','可用磁盘空间不足 512 MiB')
    from health import identities
    atomic_json(Path(config['marker']).parent/'protected-services.json',identities())

def version_key(version): return tuple(int(piece) for piece in version.split('+')[0].split('-')[0].split('.'))

def choose_release(releases):
    if not isinstance(releases,list): raise UpdateError('network','发布源返回格式不正确')
    choices=[]
    for release in releases:
        if not isinstance(release,dict) or release.get('draft') or release.get('prerelease'): continue
        tag=release.get('tag_name','')
        if not isinstance(tag,str) or not tag.startswith(PREFIX): continue
        version=tag[len(PREFIX):]
        if not valid_version(version) or '-' in version or '+' in version: continue
        name=f'{version}.software-update.tar'
        assets=[asset for asset in release.get('assets',[]) if asset.get('name')==name]
        if len(assets)!=1: continue
        asset=assets[0]; url=f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
        if asset.get('browser_download_url')!=url or type(asset.get('size')) is not int or not 0<asset['size']<=MAX_OUTER:
            raise UpdateError('network','软件发布包地址或大小不符合固定发布规则')
        choices.append({'version':version,'url':url,'size':asset['size'],'changelog':str(release.get('body') or '')[:8000]})
    return max(choices,key=lambda item:version_key(item['version']),default=None)

class RestrictedRedirect(HTTPRedirectHandler):
    def __init__(self,allow_loopback=False): self.allow_loopback=allow_loopback
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        validate_url(newurl,self.allow_loopback)
        if getattr(request,'redirect_count',0)>=3: raise UpdateError('network','下载跳转次数超限')
        result=super().redirect_request(request,fp,code,msg,headers,newurl)
        result.redirect_count=getattr(request,'redirect_count',0)+1
        return result

def validate_url(url,loopback=False):
    parts=urlsplit(url)
    if parts.username or parts.password or parts.fragment: raise UpdateError('network','下载地址无效')
    if loopback and parts.scheme=='http' and parts.hostname in {'127.0.0.1','::1','localhost'}: return
    if parts.scheme!='https' or parts.port not in (None,443) or parts.hostname not in {
        'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}:
        raise UpdateError('network','下载必须使用固定发布源的 HTTPS 地址')

def fetch(url,destination,maximum,loopback=False):
    validate_url(url,loopback)
    opener=build_opener(RestrictedRedirect(loopback))
    deadline=time.monotonic()+120; received=0
    with opener.open(Request(url,headers={'User-Agent':'Microduck-Software-Updater/1','Accept':'application/vnd.github+json' if url==CATALOG else 'application/octet-stream'}),timeout=15) as response:
        length=response.headers.get('Content-Length')
        if length and (not length.isdigit() or int(length)>maximum): raise UpdateError('network','下载大小超限')
        while True:
            chunk=response.read(min(1024*1024,maximum-received+1))
            if not chunk: break
            received+=len(chunk)
            if received>maximum or time.monotonic()>deadline: raise UpdateError('network','下载大小或时间超限')
            destination.write(chunk)
    return received

class Controller:
    def __init__(self,state_dir,engine=None,preflight=None,catalog=None,marker=None,fetcher=fetch):
        self.root=Path(state_dir); self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.engine=engine or Engine(); self.preflight=preflight or (lambda:None); self.catalog=catalog or self.remote_catalog
        self.fetcher=fetcher; self.can_rollback=False
        self.marker=Path(marker) if marker else self.root/'installing'
        self.motion_descriptor=None
        self.marker.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
        descriptor=os.open(self.marker.parent/'motion.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o644)
        os.close(descriptor)
        self.lock=threading.Lock(); self.state_lock=threading.RLock(); self.busy=False
        self.job=read_json(self.root/'job.json'); self.staged=read_json(self.root/'staged.json')
        self.ready=False; self.candidate=None; self.source_state='unchecked'; self.engine_status=None;self.dispatched=False
        # A restarted coordinator does NOT infer success from its own counter. It
        # keeps the mutation gate while reconciling the engine's durable outcome.
        if self.job and self.job.get('phase') not in TERMINAL:
            self.busy=True; self.lock.acquire()
            threading.Thread(target=self.recover,daemon=True).start()
        else: self.marker.unlink(missing_ok=True)

    def remote_catalog(self):
        import io
        def get(url):
            buffer=io.BytesIO();fetch(url,buffer,2*1024*1024);return json.loads(buffer.getvalue())
        releases=get(CATALOG)
        if not isinstance(releases,list):raise UpdateError('network','发布源返回格式不正确')
        # Some unauthenticated GitHub list responses omit assets. Fetch only
        # matching formal releases, newest first, bounded to three detail calls.
        missing=[entry for entry in releases if isinstance(entry,dict) and not entry.get('assets')
            and not entry.get('draft') and not entry.get('prerelease')
            and str(entry.get('tag_name','')).startswith(PREFIX)
            and valid_version(str(entry['tag_name'])[len(PREFIX):]) and type(entry.get('id')) is int]
        for entry in sorted(missing,key=lambda item:version_key(item['tag_name'][len(PREFIX):]),reverse=True)[:3]:
            detail=get(f'https://api.github.com/repos/{REPOSITORY}/releases/{entry["id"]}')
            if not isinstance(detail,dict) or detail.get('id')!=entry['id'] or detail.get('tag_name')!=entry['tag_name']:
                raise UpdateError('network','发布详情与目录不一致')
            releases[releases.index(entry)]=detail
        return releases

    def save(self,**values):
        with self.state_lock:
            self.job.update(values,updated_at=time.time()); atomic_json(self.root/'job.json',self.job)

    def refresh(self):
        try:
            status=self.engine.call('update.status')
            self.engine_status=next((entry for entry in status if entry.get('component')=='software'),None)
            installed=self.engine.call('update.listInstalled',{'component':'software'})
            self.can_rollback=any(not entry.get('active') for entry in installed)
        except (OSError,ValueError,UpdateError): self.engine_status=None

    def view(self):
        with self.state_lock:
            return {'available':True,'busy':self.busy,'ready':self.ready,
                'installed':(self.engine_status or {}).get('installed'),'engine_available':self.engine_status is not None,
                'can_rollback':self.can_rollback,
                'source_state':self.source_state,'candidate':{key:value for key,value in (self.candidate or {}).items() if key!='url'} or None,
                'staged':{key:value for key,value in (self.staged or {}).items() if key in {'version','changelog','source_revision'}} or None,
                'job':dict(self.job) if self.job else None}

    def start(self,operation):
        if not isinstance(operation,str) or operation not in {'check','download','validate','install','rollback'}: raise UpdateError('validation','不支持的更新操作')
        if operation=='install' and not self.ready: raise UpdateError('validation','请先让升级引擎完成软件包验证')
        if not self.lock.acquire(blocking=False): raise UpdateError('busy','另一项更新任务正在进行')
        self.busy=True
        self.job={'id':uuid.uuid4().hex,'operation':operation,'phase':'queued','started_at':time.time(),'error':None}
        self.save()
        threading.Thread(target=self.run,args=(operation,),daemon=True).start()
        return self.view()

    def upload(self,source):
        if not self.lock.acquire(blocking=False): raise UpdateError('busy','另一项更新任务正在进行')
        self.busy=True; self.ready=False
        self.job={'id':uuid.uuid4().hex,'operation':'upload','phase':'uploading','started_at':time.time(),'error':None}; self.save()
        try: self.stage(source)
        except Exception:
            self.save(phase='failed',error='更新包格式无效'); self.busy=False; self.lock.release(); raise
        threading.Thread(target=self.run,args=('validate',),daemon=True).start()
        return self.view()

    def stage(self,source):
        cache=self.root/'cache'; cache.mkdir(exist_ok=True,mode=0o700)
        # Bound storage to two prior cached packages plus the in-flight envelope.
        for old in sorted(cache.iterdir(),key=lambda path:path.stat().st_mtime)[:-2]:
            if old.is_dir() and not old.is_symlink(): shutil.rmtree(old)
        temporary=cache/('.incoming-'+uuid.uuid4().hex)
        manifest=unpack_outer(source,temporary)
        target=cache/manifest['version']
        if target.exists(): shutil.rmtree(target)
        temporary.rename(target)
        self.staged={**manifest,'fingerprint':self.fingerprint(target)}
        atomic_json(self.root/'staged.json',self.staged); self.ready=False
        self.save(phase='downloaded',version=manifest['version'])

    def fingerprint(self,path):
        entries=sorted(path.iterdir())
        if len(entries)!=4: raise UpdateError('cache','缓存文件发生变化，请重新上传或下载')
        digest=hashlib.sha256()
        for file in entries:
            if not file.is_file() or file.is_symlink() or file.stat().st_nlink!=1: raise UpdateError('cache','缓存文件类型不正确')
            digest.update(file.name.encode())
            with file.open('rb') as stream:
                while chunk:=stream.read(1024*1024): digest.update(chunk)
        return digest.hexdigest()

    def validate(self,apply=False):
        if not self.staged or not valid_version(self.staged.get('version')): raise UpdateError('validation','请先下载或上传软件包')
        directory=self.root/'cache'/self.staged['version']
        if self.fingerprint(directory)!=self.staged['fingerprint']:
            self.ready=False; raise UpdateError('cache','缓存校验值发生变化，请重新下载或上传')
        self.ready=False; self.save(phase='preflight'); self.preflight()
        self.save(phase='verifying' if not apply else 'applying',version=self.staged['version'])
        options={'from_dir':str(directory)}
        if not apply: options['dry_run']=True
        self.dispatched=apply
        result=self.engine.call('update.apply',{'component':'software','target':{'exact':self.staged['version']},'options':options},self.progress)
        self.dispatched=False
        outcome=result.get('outcome')
        if not apply and outcome=='dry_run_passed':
            self.ready=True; self.save(phase='completed',result=result); return
        self.finish_result(result)

    def finish_result(self,result):
        outcome=result.get('outcome')
        if outcome=='rolled_back': self.save(phase='rolled_back',result=result,error=result.get('reason','更新失败，已恢复旧版'))
        elif outcome in {'applied','already_current'}: self.save(phase='completed',result=result)
        else: raise UpdateError('engine',str(result.get('reason','升级引擎未确认操作成功'))[:500])

    def progress(self,progress):
        if progress.get('component')=='software':
            self.save(phase=str(progress.get('phase','applying'))[:50],percent=progress.get('percent'),detail=str(progress.get('detail') or '')[:500])

    def enter_motion_gate(self,timeout=15):
        if self.motion_descriptor is not None or os.name!='posix':return
        import fcntl
        descriptor=os.open(self.marker.parent/'motion.lock',os.O_RDWR|getattr(os,'O_NOFOLLOW',0))
        deadline=time.monotonic()+timeout if timeout is not None else None
        try:
            while True:
                try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if deadline is not None and time.monotonic()>deadline:
                        raise UpdateError('preflight','语音动作尚未结束，请等待停稳后重试')
                    time.sleep(.05)
            self.motion_descriptor=descriptor
        except Exception:os.close(descriptor);raise

    def leave_motion_gate(self):
        if self.motion_descriptor is not None:
            os.close(self.motion_descriptor);self.motion_descriptor=None

    def run(self,operation):
        try:
            if operation in MUTATIONS:
                self.marker.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                self.marker.write_text(self.job['id']); os.chmod(self.marker,0o644)
                self.enter_motion_gate()
            if operation=='check':
                self.save(phase='checking'); self.refresh(); self.candidate=choose_release(self.catalog())
                installed=(self.engine_status or {}).get('installed')
                self.source_state='no_release' if not self.candidate else 'up_to_date' if installed and version_key(installed)>=version_key(self.candidate['version']) else 'available'
                self.save(phase='completed',source_state=self.source_state)
            elif operation=='download':
                if not self.candidate: raise UpdateError('validation','请先检查更新')
                self.save(phase='downloading'); self.ready=False
                with tempfile.TemporaryFile(dir=self.root) as stream:
                    size=self.fetcher(self.candidate['url'],stream,MAX_OUTER)
                    if size!=self.candidate['size']: raise UpdateError('network','下载大小与发布元信息不一致')
                    stream.seek(0); self.stage(stream)
                self.marker.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                self.marker.write_text(self.job['id']); os.chmod(self.marker,0o644)
                self.enter_motion_gate()
                self.validate()
            elif operation in {'validate','install'}: self.validate(apply=operation=='install')
            elif operation=='rollback':
                self.save(phase='preflight'); self.preflight()
                self.dispatched=True
                self.finish_result(self.engine.call('update.rollback',{'component':'software'},self.progress))
                self.dispatched=False
        except UpdateError as error:
            if self.dispatched:
                self.save(phase='recovering',error='引擎连接中断，正在读取持久任务结果');self.recover(release=False)
            elif operation in {'download','validate'} and error.code=='preflight': self.save(phase='waiting_validation',error=str(error))
            else:
                if operation=='check': self.source_state='network_error'
                self.save(phase='failed',error=str(error)[:500],code=error.code)
        except Exception:
            if self.dispatched:
                self.save(phase='recovering',error='引擎连接中断，正在读取持久任务结果');self.recover(release=False)
            else:
                if operation=='check': self.source_state='network_error'
                self.save(phase='failed',error='更新服务未完成操作；请检查服务和网络后重试',code='service')
        finally:
            self.refresh();self.leave_motion_gate();self.marker.unlink(missing_ok=True);self.busy=False;self.lock.release()

    def recover(self,release=True):
        # A controller crash cannot cancel updaterd. Reconcile only the engine's
        # durable attempt; never repeat an apply or infer completion by version.
        try:
            self.marker.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
            self.marker.write_text(self.job['id']);self.marker.chmod(0o644)
            self.enter_motion_gate(timeout=None)
            while True:
                self.refresh(); state=self.engine_status
                if state and state.get('phase')=='idle':
                    attempt=state.get('last_attempt') or {}
                    if attempt.get('at',0)>=int(self.job['started_at']):
                        kind=(attempt.get('outcome') or {}).get('kind')
                        if kind=='success': self.save(phase='completed',result={'outcome':'applied','to':attempt.get('to')},engine_attempt=attempt)
                        elif kind=='rolled_back': self.save(phase='rolled_back',error=attempt['outcome'].get('reason','已恢复旧版'),engine_attempt=attempt)
                        elif kind=='aborted': self.save(phase='failed',error=attempt['outcome'].get('reason','引擎拒绝更新'),engine_attempt=attempt)
                        else: self.save(phase='interrupted',error='引擎任务已结束，请核对记录后重新验证',engine_attempt=attempt)
                    else: self.save(phase='interrupted',error='更新请求曾中断；引擎当前空闲，未发现本次完成记录，请重新验证')
                    return
                time.sleep(1)
        finally:
            if release:
                self.leave_motion_gate();self.marker.unlink(missing_ok=True);self.busy=False;self.lock.release()
