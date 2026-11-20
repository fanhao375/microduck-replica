"""Build the reproducible, independently signed preparation zipapp from pinned Git blobs."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
STOCK = 'f215b658a4f24b669cf664b6381a559c61472009'
FIX = 'a9ef4020a610fb7f4948ec49845e8d23c9fbb432'
UPDATER = 'software/updater/'
STUDIO = 'software/studio/studio/'

def blob(ref, path):
    return subprocess.check_output(['git', '-C', str(HERE), 'show', ref + ':' + path])

def readiness_span(data):
    lines = data.decode().splitlines(keepends=True)
    build = next(n for n in ast.parse(data).body if isinstance(n, ast.FunctionDef) and n.name == 'build_app')
    ready, = [n for n in build.body if isinstance(n, ast.FunctionDef) and n.name == 'update_readiness']
    return ''.join(lines[:ready.lineno-1]), ''.join(lines[ready.lineno-1:ready.end_lineno]), ''.join(lines[ready.end_lineno:])

def unit_bytes():
    # Factory bootstrap writes these UTF-8 strings literally; only ENGINE/CONTROL interpolate STABLE.
    code = ast.parse(blob(STOCK, UPDATER + 'bootstrap.py'))
    units = {}
    for node in code.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {'ENGINE', 'CONTROL', 'STUDIO'}:
                parts = node.value.values if isinstance(node.value, ast.JoinedStr) else [node.value]
                text = ''.join(p.value if isinstance(p, ast.Constant) else '/usr/local/lib/microduck-updater' for p in parts)
                units[name] = hashlib.sha256(text.encode()).hexdigest()
                if name == 'STUDIO':
                    override = '[Service]\nExecStart=\n' + next(s for s in text.splitlines() if s.startswith('ExecStart=')) + '\n'
                    units['OVERRIDE'] = hashlib.sha256(override.encode()).hexdigest()
    return units

def zip_source(name):
    # Normalize checkout line endings and EOF for reproducible source entries.
    return ((HERE/name).read_text(encoding='utf-8').rstrip('\n')+'\n\n').encode()

def bundle():
    originals = {name: blob(STOCK, (STUDIO if name in ('updates.py','app.py') else UPDATER) + name)
                 for name in ('app.py','updates.py','control.py','health.py','package.py','run_control.py','service.py','engine_systemctl.py')}
    before, _, after = readiness_span(originals['app.py'])
    patched = (before + readiness_span(blob(FIX, STUDIO + 'app.py'))[1] + after).encode()
    payload = {'control.py': blob(FIX, UPDATER + 'control.py'), 'health.py': blob(FIX, UPDATER + 'health.py'),
               'updates.py': blob(FIX, STUDIO + 'updates.py'), 'app.py': patched,
               'launch.py': zip_source('launch.py')}
    manifest = {'stock': STOCK, 'fix': FIX, 'version': '2026.11.4', 'units': unit_bytes(),
                'originals': {k: hashlib.sha256(v).hexdigest() for k,v in originals.items()}}
    entries = {'manifest.json': json.dumps(manifest, sort_keys=True).encode(),
               '__main__.py': zip_source('install.py'),
               'control.py': payload['control.py'], 'package.py': blob(FIX, UPDATER + 'package.py'),
               **{'payload/'+k:v for k,v in payload.items()}}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2026,11,4,0,0,0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)  # Stored avoids zlib-version-dependent output.
    return output.getvalue(), manifest

if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('output', type=Path)
    args = cli.parse_args()
    data, _ = bundle()
    args.output.write_bytes(data)
    print(hashlib.sha256(data).hexdigest(), args.output)
