"""Install the signed software release into an offline image using its real bootstrap."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile

from controller_factory import install as install_controller_factory


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def safe_target(root, relative):
    target = root / relative
    for parent in (target, *target.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('unsafe rootfs symlink: '+str(parent))
    if not target.resolve().is_relative_to(root):
        raise ValueError('unsafe rootfs target')
    return target


def validate_resources(resources):
    manifest_file = resources/'resources-manifest.json'
    if resources.is_symlink() or manifest_file.is_symlink() or manifest_file.stat().st_size > 4*1024*1024:
        raise ValueError('unsafe resources manifest')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or manifest.get('target') != 'linux-aarch64-cp313':
        raise ValueError('unsupported voice resources target')
    files = manifest.get('files')
    if not isinstance(files,list) or not files or len(files)>20000:
        raise ValueError('invalid resources file list')
    declared, total = set(), 0
    for item in files:
        relative = item.get('path')
        if (not isinstance(relative,str) or not relative or '\\' in relative or ':' in relative
                or relative.startswith('/') or '..' in PurePosixPath(relative).parts
                or relative.split('/')[0] not in ('models','asr-deps') or relative in declared):
            raise ValueError('unsafe resources member')
        declared.add(relative)
        path = safe_target(resources.resolve(),relative)
        if (not path.is_file() or path.stat().st_size != item.get('size')
                or digest(path) != item.get('sha256')):
            raise ValueError('voice resource hash mismatch: '+relative)
        total += path.stat().st_size
        if total>512*1024*1024:
            raise ValueError('voice resources exceed size limit')
    for path in resources.rglob('*'):
        if path.is_symlink():
            raise ValueError('unsafe resource symlink')
        if path.is_file() and path != manifest_file and path.relative_to(resources).as_posix() not in declared:
            raise ValueError('unlisted resource file')
    if ('asr-deps/vosk/__init__.py' not in declared
            or 'models/vosk-model-small-cn-0.22/am/final.mdl' not in declared):
        raise ValueError('voice resource essentials missing')
    return manifest


def install(root, package, public_key, engine, resources, *, bootstrap_script=None,
            engine_command=None, runner=subprocess.run):
    root, package, public_key, engine, resources = map(Path,(root,package,public_key,engine,resources))
    root = root.resolve(strict=True)
    if root == Path(root.anchor) or not safe_target(root,'etc').is_dir():
        raise ValueError('offline factory rootfs required')
    bootstrap_script = Path(bootstrap_script or Path(__file__).resolve().parents[1]/'updater/bootstrap.py')
    for source in (package,public_key,engine,bootstrap_script):
        if not source.is_file():
            raise ValueError('component input missing: '+str(source))
    manifest = validate_resources(resources)
    destination = safe_target(root,'opt/microduck/resources')
    if destination.exists():
        if validate_resources(destination) != manifest:
            raise ValueError('existing resources differ; refusing overwrite')
    else:
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.resources-',dir=destination.parent))
        try:
            for item in manifest['files']:
                target = temporary/item['path']
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(resources/item['path'],target)
                target.chmod(0o644)
            shutil.copyfile(resources/'resources-manifest.json',temporary/'resources-manifest.json')
            for folder in (temporary,*[p for p in temporary.rglob('*') if p.is_dir()]):
                folder.chmod(0o755)
            validate_resources(temporary)
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    command = [sys.executable,str(bootstrap_script),'--root',str(root),'--package',str(package),
               '--public-key',str(public_key),'--engine',str(engine),'--offline']
    if engine_command:
        command += ['--engine-command-json',json.dumps(engine_command)]
    runner(command,check=True,capture_output=True,text=True,timeout=240)
    controller = install_controller_factory(root)
    record = {'schema':1,'package_sha256':digest(package),'engine_sha256':digest(engine),
              'public_key_sha256':digest(public_key),'resources_manifest_sha256':digest(resources/'resources-manifest.json'),
              'resource_files':len(manifest['files']),'voice':'local-sit-stand-actions-toggle-duck'}
    if controller is not None:
        record['controller_bundle'] = controller
    target = safe_target(root,'usr/share/doc/microduck/software-components.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    target.chmod(0o644)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','package','public-key','engine','resources'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--engine-command-json',default='[]')
    args=parser.parse_args()
    command=json.loads(args.engine_command_json)
    if not isinstance(command,list) or any(not isinstance(x,str) for x in command):
        parser.error('engine command must be an explicit JSON string array')
    try:
        result=install(args.root,args.package,args.public_key,args.engine,args.resources,engine_command=command)
    except subprocess.CalledProcessError as error:
        print((error.stdout or '')[-12000:],file=sys.stderr)
        print((error.stderr or '')[-12000:],file=sys.stderr)
        raise
    print(json.dumps(result))


if __name__=='__main__':
    main()
