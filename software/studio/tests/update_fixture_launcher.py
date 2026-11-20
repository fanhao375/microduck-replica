"""Isolated browser launcher: real update coordinator, explicit fake transaction engine."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'deps'),str(root)]
spec=importlib.util.spec_from_file_location('update_fixture',root.parent/'updater/tests/e2e_fixture.py')
fixture_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
output=Path(os.environ['UPDATE_E2E_DIR'])
fixture=fixture_module.Fixture(output/'controller',os.environ['UPDATE_E2E_PACKAGE'],marker=output/'installing')
record_lock=threading.Lock()
original_call=fixture.engine.call
def record(method,params=None,progress=None):
    with record_lock,(output/'engine.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'method':method,'params':params},ensure_ascii=False)+'\n')
    return original_call(method,params,progress)
fixture.engine.call=record
legacy_host=True
current_view=fixture.controller.view
def view():
    result=current_view()
    if legacy_host:result.pop('online_update',None)
    return result
fixture.controller.view=view
server=fixture.serve(int(os.environ['UPDATE_FIXTURE_PORT']))

from studio import app as app_module
from studio import helper
from studio.updates import Updates
original_build=app_module.build_app

# Only this explicit local test launcher supplies a native backend. Production
# never reads fixture files and always calls the installed trusted root helper.
native_root=output/'native-root'
native_path=native_root/helper.NATIVE_UPDATE_JOURNAL.lstrip('/')
native_path.parent.mkdir(parents=True,exist_ok=True)
native_lock=threading.Lock()
native={'active':False,'phase':'idle','available':True,'running_verified':True,
        'installed_sha256':'e'*64,'target_sha256':'f'*64,'mode':'motion'}
native_failure=False
def native_save(**values):
    with native_lock:
        native.update(values)
        if native.get('transaction_id'):
            bundle=bool(native.get('bundle_schema'))
            path=native_root/(helper.CONTROLLER_UPDATE_JOURNAL if bundle else helper.NATIVE_UPDATE_JOURNAL).lstrip('/')
            if bundle and native.get('phase')=='idle':
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True,exist_ok=True)
            value={**native,'schema':'microduck-controller-update/1' if bundle else 'microduck-native-update/1',
                   'restore_pending':False,'cleanup_pending':False}
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(path)

def native_call(by,action,**kwargs):
    with native_lock,(output/'native.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'by':by,'action':action,**kwargs})+'\n')
    if action=='status':
        with native_lock:
            value=dict(native)
        if fixture.engine.version in ('2026.10.26','2026.10.25'):
            return False,'bad_verb',None
        return True,'native_status',value
    if by!='Studio' or kwargs!={'confirmed':True}:return False,'bad_args',None
    if action=='install' and (not native['available'] or not native['running_verified']):return False,'native_unsupported',{'reason':'本机主程序不受支持'}
    native_save(active=True,phase='replacing' if action=='install' else 'restoring' if action=='restore' else 'recovering',transaction_id='a'*32,reason=None)
    time.sleep(2.5)
    if action=='install' and native_failure:
        native_save(active=True,phase='recovery_required',running_verified=False,reason='模拟启动核验失败；请恢复')
    else:
        native_save(active=False,phase='completed' if action=='install' else 'rolled_back',running_verified=True,
                    installed_sha256='f'*64 if action=='install' else 'e'*64,reason=None)
        if native.get('bundle_schema'):
            native_save(phase='completed' if action=='install' else 'restored',bundle_current=action=='install',
                restorable=action=='install',installed_sha256='f'*64,
                components={'robotd':{'installed_sha256':'f'*64,'target_sha256':'f'*64},
                            'padd':{'installed_sha256':('b' if action=='install' else 'a')*64,'target_sha256':'b'*64}})
    return True,'native_complete',dict(native)

base_handler=server.RequestHandlerClass
class NativeControl(base_handler):
    def handle(self):
        global native_failure,legacy_host
        first=self.rfile.peek(8193).split(b'\n',1)[0]
        try: request=json.loads(first)
        except ValueError:return super().handle()
        if request.get('op')!='native_fixture':return super().handle()
        self.rfile.readline(8193)
        if 'legacy_host' in request:legacy_host=request.pop('legacy_host')
        if 'software_version' in request:fixture.engine.version=request.pop('software_version')
        if 'failure' in request:native_failure=request.pop('failure')
        native_save(**{key:value for key,value in request.items() if key!='op'})
        self.wfile.write(b'{"ok":true}\n')
server.RequestHandlerClass=NativeControl
def build(*args,**kwargs):
    kwargs['updates']=Updates(address=f'tcp:127.0.0.1:{server.server_address[1]}',
        marker=output/'installing',readiness=output/'readiness.sock',native_call=native_call,
        native_state=lambda:helper.native_update_state(root=str(native_root)))
    return original_build(*args,**kwargs)
app_module.build_app=build
runpy.run_path(str(root/'tests/audio_fixture_launcher.py'),run_name='__main__')
