"""Stable health gate, outside the tree updaterd swaps."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

sys.path.insert(0,str(Path(__file__).resolve().parent))
from control import json_socket,read_json

def unit(name):
    output=subprocess.check_output(['/usr/bin/systemctl','show',name,'--property=LoadState,ActiveState,SubState,MainPID,InvocationID,ExecMainStartTimestampMonotonic'],text=True,timeout=4)
    values=dict(line.split('=',1) for line in output.splitlines() if '=' in line)
    if values.get('LoadState')!='loaded' or values.get('ActiveState')!='active' or values.get('SubState')!='running' or int(values.get('MainPID','0'))<=0:
        raise RuntimeError(name+' is not running and ready')
    return values

def identities(): return {name:unit(name) for name in ('robotd','padd')}

def healthy():
    current=Path('/opt/microduck/software/current').resolve()
    metadata=json.loads((current/'suite-version.json').read_bytes())
    for name in ('microduck-studio','duck-echo'): unit(name)
    baseline=read_json('/run/microduck-updater/protected-services.json')
    if not baseline or identities()!=baseline: raise RuntimeError('robotd/padd process identities changed or were not recorded')
    with urlopen('http://127.0.0.1:8000/healthz',timeout=3) as response: studio=json.load(response)
    if studio.get('suite_version')!=metadata['version'] or studio.get('runtime_root')!=str(current):
        raise RuntimeError('Studio is still serving another release')
    with urlopen('http://127.0.0.1:8000/app.js',timeout=3) as response: data=response.read(2*1024*1024)
    if hashlib.sha256(data).hexdigest()!=metadata['files']['studio/web/app.js']:
        raise RuntimeError('Studio static content does not match current release')
    audio=json_socket('/run/duck-echo/control.sock',{'method':'status'})
    if (audio.get('ok') is not True or audio.get('status',{}).get('runtime_root')!=str(current)
        or audio.get('status',{}).get('voice_ready') is not True):
        raise RuntimeError('voice control is not ready on current release')
    resources=Path('/opt/microduck/resources')
    for name in ('resources-manifest.json','models/vosk-model-small-cn-0.22/am/final.mdl','asr-deps/vosk/__init__.py'):
        if not (resources/name).is_file(): raise RuntimeError('local speech resources are missing')
    return True

def wait_healthy(seconds):
    deadline=time.monotonic()+seconds
    while True:
        try: return healthy()
        except Exception:
            if time.monotonic()>=deadline: raise
            time.sleep(.5)

if __name__=='__main__':
    # 新版中控在 ZERO 3W 上要 10 秒左右才开始接受连接：等它起来再判（引擎给这个检查 90 秒）
    try: wait_healthy(80)
    except Exception as error:
        print('software health failed: '+str(error)[:400],file=sys.stderr); sys.exit(1)
