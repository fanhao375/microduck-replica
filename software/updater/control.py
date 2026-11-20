"""Independent, bounded update coordinator. updaterd alone verifies and swaps releases."""
import hashlib
import json
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
CHECK_INTERVAL=6*60*60
MUTATIONS={'validate','install','rollback'}
TERMINAL={'completed','rolled_back','failed','waiting_validation','interrupted'}

class UpdateError(Exception):
    def __init__(self,code,message): super().__init__(message); self.code=code

class EngineRejected(UpdateError):
    """The engine returned a final JSON-RPC error for the submitted request."""
    def __init__(self,message):
        printable=''.join(char for char in message if char.isprintable() or char.isspace())
        super().__init__('engine',' '.join(printable.split())[:500] or '升级引擎拒绝请求')

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
                        if 'error' in message:
                            error=message['error']
                            if (message.get('jsonrpc')=='2.0' and type(message['id']) is int and 'result' not in message
                                    and isinstance(error,dict) and type(error.get('code')) is int and isinstance(error.get('message'),str)):
                                raise EngineRejected(error['message'])
                            raise UpdateError('protocol','升级引擎返回了无效的错误回包')
                        return message['result']
                    if progress and isinstance(message.get('params'),dict): progress(message['params'])

def require_model_idle(robot):
    unknown = '暂时无法确认运动模型是否接管，请等主程序恢复后重新验证'
    try:
        bus = robot.call('robot.busStatus')
    except (OSError, UpdateError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise UpdateError('preflight', unknown) from error
    if not isinstance(bus, dict):
        raise UpdateError('preflight', unknown)
    if bus.get('policy_enabled') is True or bus.get('policy_motion_enabled') is True:
        raise UpdateError('preflight', '运动模型正在接管，请退出模型后重新验证')
    if 'policy_motion_enabled' in bus and bus['policy_motion_enabled'] is not False:
        raise UpdateError('preflight', unknown)
    if bus.get('policy_enabled') is False:
        return
    # 1104 的标定模式只上报 policy_motion_enabled，控制模式上报 policy_enabled。
    if (bus.get('mode') == 'commissioning' and 'policy_enabled' not in bus
            and bus.get('policy_motion_enabled') is False):
        return
    raise UpdateError('preflight', unknown)

def preflight(config):
    # The installing marker already prevents new Studio and voice actions.
    studio = json_socket(config['studio_socket'], {'op':'software-readiness'})
    if studio == {'ready':False, 'error':'不支持的内部请求'}:
        # A previously installed Studio only implements the original, stricter check.
        studio = json_socket(config['studio_socket'], {'op':'readiness'})
    if studio.get('ready') is not True:
        raise UpdateError('preflight', studio.get('error','网页控制服务尚未就绪'))
    require_model_idle(Engine(config['robot_socket']))
    if shutil.disk_usage(config['state_dir']).free<512*1024*1024:
        raise UpdateError('preflight','可用磁盘空间不足 512 MiB')
    from health import identities
    atomic_json(Path(config['marker']).parent/'protected-services.json',identities())

def version_key(version):
    core, _, pre = version.partition('+')[0].partition('-')
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part) for part in pre.split('.') if part)
    return (tuple(map(int, core.split('.'))), not pre, identifiers)

def stable_tag(release):
    if not isinstance(release,dict) or release.get('draft') is not False or release.get('prerelease') is not False:
        return None
    tag=release.get('tag_name')
    if not isinstance(tag,str) or not tag.startswith(PREFIX): return None
    version=tag[len(PREFIX):]
    return version if valid_version(version) and '-' not in version and '+' not in version else None

def choose_release(releases):
    if not isinstance(releases,list): raise UpdateError('network','发布源返回格式不正确')
    choices=[]
    for release in releases:
        version=stable_tag(release)
        if version is None: continue
        tag=release['tag_name']
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
    accept='application/vnd.github+json' if urlsplit(url).hostname=='api.github.com' else 'application/octet-stream'
    with opener.open(Request(url,headers={'User-Agent':'Microduck-Software-Updater/1','Accept':accept}),timeout=15) as response:
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
        self.fetcher=fetcher; self.previous_versions=[]
        self.marker=Path(marker) if marker else self.root/'installing'
        self.motion_descriptor=None
        self.marker.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
        descriptor=os.open(self.marker.parent/'motion.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o644)
        os.close(descriptor)
        self.lock=threading.Lock(); self.state_lock=threading.RLock(); self.refresh_lock=threading.Lock(); self.busy=False
        self.job=read_json(self.root/'job.json'); self.staged=read_json(self.root/'staged.json')
        self.ready=False; self.engine_status=None; self.installed=None; self.dispatched=False; self.catalog_checking=False
        cached=read_json(self.root/'catalog.json') or {}
        self.candidate=cached.get('candidate')
        self.catalog_checked_at=cached.get('checked_at')
        self.source_state=cached.get('source_state','unchecked')
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
        missing=[entry for entry in releases if stable_tag(entry) and not entry.get('assets') and type(entry.get('id')) is int]
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
        with self.refresh_lock:
            state,active,previous=self.engine_identity()
            with self.state_lock:
                self.engine_status,self.installed,self.previous_versions=state,active,previous

    def engine_identity(self):
        state=None
        active=None
        previous=[]
        try:
            status=self.engine.call('update.status')
            software=[entry for entry in status if entry.get('component')=='software']
            if len(software)==1:
                state=software[0]
                installed=self.engine.call('update.listInstalled',{'component':'software'})
                versions=[entry.get('version') for entry in installed if entry.get('active') is True]
                if versions==[state.get('installed')] and valid_version(versions[0]):
                    active=versions[0]
                    previous=[entry['version'] for entry in installed if entry.get('active') is False
                              and valid_version(entry.get('version')) and version_key(entry['version'])<version_key(active)]
        except (OSError,ValueError,UpdateError,TypeError,AttributeError,KeyError): pass
        return state,active,previous

    def compare_installed(self,version):
        if not self.installed or (self.engine_status or {}).get('phase')!='idle': return 'installed_unknown'
        return 'available' if version_key(self.installed)<version_key(version) else 'up_to_date'

    def availability(self):
        if not self.candidate: return self.source_state
        current=self.compare_installed(self.candidate['version'])
        if current=='available' and self.source_state!='available': return 'unchecked'
        return current

    def catalog_fresh(self):
        return self.catalog_checked_at is not None and 0<=time.time()-self.catalog_checked_at<CHECK_INTERVAL

    def check_catalog(self):
        try:
            candidate=choose_release(self.catalog())
        except Exception:
            self.record_catalog(None,'network_error')
            raise
        self.record_catalog(candidate,self.compare_installed(candidate['version']) if candidate else 'no_release')

    def record_catalog(self,candidate,source_state):
        with self.state_lock:
            self.candidate,self.source_state,self.catalog_checked_at=candidate,source_state,time.time()
            atomic_json(self.root/'catalog.json',{'candidate':candidate,'source_state':source_state,'checked_at':self.catalog_checked_at})

    def require_online(self,version):
        with self.state_lock:
            if not self.catalog_fresh() or not self.candidate or self.candidate['version']!=version:
                raise UpdateError('validation','可用版本已改变或检查已过期，请重新检查更新')
            if self.availability()!='available':
                raise UpdateError('validation','未能确认当前安装状态和可升级版本，请重新检查更新')

    def view(self):
        with self.state_lock:
            return {'available':True,'busy':self.busy,'ready':self.ready,'online_update':True,
                'channel':'stable','catalog_checked_at':self.catalog_checked_at,'catalog_checking':self.catalog_checking,
                'installed':self.installed,'engine_available':self.engine_status is not None,
                'can_rollback':bool(self.previous_versions),
                'source_state':self.availability(),'candidate':{key:value for key,value in (self.candidate or {}).items() if key!='url'} or None,
                'staged':{key:value for key,value in (self.staged or {}).items() if key in {'version','changelog','source_revision'}} or None,
                'job':{key:value for key,value in self.job.items() if key!='dispatch'} if self.job else None}

    def start(self,operation):
        if not isinstance(operation,str): raise UpdateError('validation','不支持的更新操作')
        version=None
        if operation.startswith('online:'):
            version=operation[len('online:'):]
            if not valid_version(version) or '-' in version or '+' in version: raise UpdateError('validation','无效的正式软件版本')
            operation='online'
        elif operation not in {'check','check_auto','download','validate','install','rollback'}:
            raise UpdateError('validation','不支持的更新操作')
        if not self.lock.acquire(blocking=False):
            if operation=='check_auto': return self.view()
            raise UpdateError('busy','另一项更新任务正在进行')
        started=False
        try:
            with self.state_lock:
                if operation=='check_auto' and self.catalog_fresh():
                    return self.view()
                if operation=='online': self.require_online(version)
                if operation=='install' and not self.ready: raise UpdateError('validation','请先让升级引擎完成软件包验证')
                if operation!='check_auto':
                    self.job={'id':uuid.uuid4().hex,'operation':operation,'phase':'queued','started_at':time.time(),'error':None}
                    if version: self.job['version']=version
                    self.save()
                self.busy=True
                self.catalog_checking=operation in {'check','check_auto'}
                threading.Thread(target=self.run,args=(operation,),daemon=True).start()
                started=True
                return self.view()
        finally:
            if not started:
                self.busy=False; self.catalog_checking=False
                self.lock.release()

    def upload(self,source):
        if not self.lock.acquire(blocking=False): raise UpdateError('busy','另一项更新任务正在进行')
        self.busy=True; self.ready=False
        self.job={'id':uuid.uuid4().hex,'operation':'upload','phase':'uploading','started_at':time.time(),'error':None}; self.save()
        try: self.stage(source)
        except Exception:
            self.save(phase='failed',error='更新包格式无效'); self.busy=False; self.lock.release(); raise
        threading.Thread(target=self.run,args=('validate',),daemon=True).start()
        return self.view()

    def stage(self,source,expected_version=None):
        cache=self.root/'cache'; cache.mkdir(exist_ok=True,mode=0o700)
        # Bound storage to two prior cached packages plus the in-flight envelope.
        for old in sorted(cache.iterdir(),key=lambda path:path.stat().st_mtime)[:-2]:
            if old.is_dir() and not old.is_symlink(): shutil.rmtree(old)
        temporary=cache/('.incoming-'+uuid.uuid4().hex)
        manifest=unpack_outer(source,temporary)
        if expected_version is not None and manifest['version']!=expected_version:
            shutil.rmtree(temporary)
            raise UpdateError('validation','下载包的清单版本与选定的正式版不一致')
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
        self.ready=False
        version=self.staged['version']
        online=self.job['operation']=='online'
        if online or apply:
            self.refresh()
            if online: self.require_online(version)
        self.save(phase='preflight')
        self.preflight()
        if apply: self.mark_dispatch(version)
        else: self.save(phase='verifying',version=version)
        options={'from_dir':str(directory)}
        if not apply: options['dry_run']=True
        result=self.engine.call('update.apply',{'component':'software','target':{'exact':version},'options':options},self.progress)
        if not apply:
            if result.get('outcome')!='dry_run_passed' or result.get('candidate')!=version:
                raise UpdateError('engine','升级引擎未确认选定版本的软件包验证成功')
            if not online:
                self.ready=True
                self.save(phase='completed',result=result)
            return
        self.dispatched=False
        if result.get('outcome')=='applied' and result.get('to')!=version:
            raise UpdateError('engine','升级引擎返回的已安装版本与选定版本不一致')
        if result.get('outcome')=='already_current' and result.get('version')!=version:
            raise UpdateError('engine','升级引擎返回的当前版本与选定版本不一致')
        self.finish_result(result)

    def mark_dispatch(self,version):
        state=self.engine_status or {}
        if state.get('phase')!='idle':
            raise UpdateError('preflight','未能确认升级引擎当前空闲，请重新验证')
        dispatch={'at':int(time.time()),'from':state.get('installed'),'to':version,'before':state.get('last_attempt')}
        if self.job['operation']=='rollback': dispatch['previous_versions']=list(self.previous_versions)
        self.save(phase='applying',dispatch=dispatch,version=version)
        self.dispatched=True

    def finish_result(self,result):
        outcome=result.get('outcome')
        if outcome=='rolled_back': self.save(phase='rolled_back',result=result,error=result.get('reason','更新失败，已恢复旧版'))
        elif outcome in {'applied','already_current'}: self.save(phase='completed',result=result)
        else: raise UpdateError('engine',str(result.get('reason','升级引擎未确认操作成功'))[:500])

    def progress(self,progress):
        if progress.get('component')=='software':
            phase=str(progress.get('phase','applying'))[:50]
            if phase not in TERMINAL:
                self.save(phase=phase,percent=progress.get('percent'),detail=str(progress.get('detail') or '')[:500])

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
                        raise UpdateError('preflight','语音正在执行动作，请等这条指令结束后重试')
                    time.sleep(.05)
            self.motion_descriptor=descriptor
        except Exception:os.close(descriptor);raise

    def leave_motion_gate(self):
        if self.motion_descriptor is not None:
            os.close(self.motion_descriptor);self.motion_descriptor=None

    def begin_mutation(self):
        self.marker.write_text(self.job['id'])
        self.marker.chmod(0o644)
        self.enter_motion_gate()

    def download(self):
        if not self.candidate: raise UpdateError('validation','请先检查更新')
        self.save(phase='downloading')
        self.ready=False
        with tempfile.TemporaryFile(dir=self.root) as stream:
            size=self.candidate['size']
            self.fetcher(self.candidate['url'],stream,size)
            if stream.tell()!=size: raise UpdateError('network','下载大小与发布元信息不一致')
            stream.seek(0)
            self.stage(stream,expected_version=self.candidate['version'])

    def run(self,operation):
        try:
            if operation in MUTATIONS:
                self.begin_mutation()
            if operation in {'check','check_auto'}:
                if operation=='check': self.save(phase='checking')
                self.refresh()
                self.check_catalog()
                if operation=='check': self.save(phase='completed',source_state=self.availability())
            elif operation in {'download','online'}:
                if operation=='online':
                    self.refresh()
                    self.require_online(self.job['version'])
                self.download()
                self.begin_mutation()
                self.validate()
                if operation=='online': self.validate(apply=True)
            elif operation in {'validate','install'}: self.validate(apply=operation=='install')
            elif operation=='rollback':
                self.refresh()
                self.save(phase='preflight'); self.preflight()
                self.mark_dispatch(None)
                result=self.engine.call('update.rollback',{'component':'software'},self.progress)
                self.dispatched=False
                self.finish_result(result)
        except UpdateError as error:
            if self.dispatched and not isinstance(error,EngineRejected):
                self.save(phase='recovering',error='引擎连接中断，正在读取持久任务结果');self.recover(release=False)
            elif operation in {'download','online','validate','install'} and error.code=='preflight':
                self.ready=False
                self.save(phase='waiting_validation',error=str(error))
            elif operation!='check_auto':
                self.save(phase='failed',error=str(error)[:500],code=error.code)
        except Exception:
            if self.dispatched:
                self.save(phase='recovering',error='引擎连接中断，正在读取持久任务结果');self.recover(release=False)
            elif operation!='check_auto':
                self.save(phase='failed',error='更新服务未完成操作；请检查服务和网络后重试',code='service')
        finally:
            try:
                if operation not in {'check','check_auto'}: self.refresh()
                self.leave_motion_gate()
                self.marker.unlink(missing_ok=True)
            finally:
                with self.state_lock:
                    self.dispatched=False; self.catalog_checking=False; self.busy=False
                self.lock.release()

    def recover(self,release=True):
        # A controller crash cannot cancel updaterd. Reconcile only the engine's
        # durable attempt; never repeat an apply or infer completion by version.
        try:
            if self.job.get('operation')=='check':
                self.save(phase='interrupted',error='上次检查中断，请重新检查更新')
                return
            if self.job.get('operation')=='online' and 'dispatch' not in self.job:
                self.save(phase='interrupted',error='更新在提交安装前中断，请重新检查更新')
                return
            self.marker.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
            self.marker.write_text(self.job['id']);self.marker.chmod(0o644)
            self.enter_motion_gate(timeout=None)
            while True:
                self.refresh(); state=self.engine_status
                if state and state.get('phase')=='idle':
                    self.reconcile(state.get('last_attempt'))
                    return
                time.sleep(1)
        finally:
            if release:
                self.leave_motion_gate();self.marker.unlink(missing_ok=True);self.busy=False;self.lock.release()

    def matches_dispatch(self,attempt):
        dispatch=self.job.get('dispatch')
        if not isinstance(dispatch,dict) or not isinstance(attempt,dict) or not isinstance(attempt.get('outcome'),dict): return False
        if type(dispatch.get('at')) is not int or type(attempt.get('at')) is not int or attempt['at']<dispatch['at']: return False
        if attempt.get('component')!='software' or attempt.get('from')!=dispatch.get('from'): return False
        before=dispatch.get('before')
        if attempt==before: return False
        if isinstance(before,dict) and type(before.get('run')) is int:
            if type(attempt.get('run')) is not int or attempt['run']<=before['run']: return False
        if self.job.get('operation')=='rollback':
            return attempt.get('to') in dispatch.get('previous_versions',[])
        return valid_version(dispatch.get('to')) and attempt.get('to')==dispatch['to']==self.job.get('version')

    def reconcile(self,attempt):
        if self.matches_dispatch(attempt):
            outcome=attempt['outcome']
            if outcome.get('kind')=='success' and self.installed==attempt['to']:
                self.save(phase='completed',result={'outcome':'applied','to':attempt['to']},error=None,engine_attempt=attempt)
                return
            if outcome.get('kind')=='rolled_back' and self.installed==attempt['from']:
                self.save(phase='rolled_back',error=outcome.get('reason','已恢复旧版'),engine_attempt=attempt)
                return
            if outcome.get('kind')=='aborted':
                self.save(phase='failed',error=outcome.get('reason','引擎拒绝更新'),engine_attempt=attempt)
                return
        self.save(phase='interrupted',error='引擎当前空闲，无法确认本次更新结果，请核对已安装版本后重新验证')
