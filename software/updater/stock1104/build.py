"""Build the reproducible, independently signed preparation zipapp from pinned Git blobs."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
STOCK = 'f215b658a4f24b669cf664b6381a559c61472009'
FIX = 'a9ef4020a610fb7f4948ec49845e8d23c9fbb432'
UPDATER = 'software/updater/'
STUDIO = 'software/studio/studio/'
WEB = 'software/studio/web/'
STATIC = ('settings-updates.js', 'updates-ui-state.js')

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

def bundle(ref, suite_manifest_sha256):
    if not re.fullmatch('[a-f0-9]{40}', ref) or subprocess.check_output(
            ['git','-C',str(HERE),'cat-file','-t',ref]).strip() != b'commit':
        raise ValueError('--ref must be a full Git commit ID')
    if not re.fullmatch('[a-f0-9]{64}', suite_manifest_sha256):
        raise ValueError('--suite-manifest-sha256 must be the SHA-256 of the signed stable suite-version.json')
    originals = {name: blob(STOCK, (STUDIO if name in ('updates.py','app.py') else UPDATER) + name)
                 for name in ('app.py','updates.py','control.py','health.py','package.py','run_control.py','service.py','engine_systemctl.py')}
    originals.update({name: blob(STOCK, WEB + name) for name in STATIC})
    before, _, after = readiness_span(originals['app.py'])
    patched = (before + readiness_span(blob(FIX, STUDIO + 'app.py'))[1] + after).encode()
    payload = {'control.py': blob(ref, UPDATER + 'control.py'), 'health.py': blob(FIX, UPDATER + 'health.py'),
               'updates.py': blob(ref, STUDIO + 'updates.py'), 'app.py': patched,
               'launch.py': blob(ref, UPDATER + 'stock1104/launch.py'),
               **{name: blob(ref, WEB + name) for name in STATIC}}
    manifest = {'stock': STOCK, 'fix': FIX, 'ref': ref, 'version': '2026.11.4', 'units': unit_bytes(),
                'stable_manifest_sha256': suite_manifest_sha256,
                'payload': {k: hashlib.sha256(v).hexdigest() for k,v in payload.items()},
                'originals': {k: hashlib.sha256(v).hexdigest() for k,v in originals.items()}}
    entries = {'manifest.json': json.dumps(manifest, sort_keys=True).encode(),
               '__main__.py': blob(ref, UPDATER + 'stock1104/install.py'),
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
    cli.add_argument('--ref', required=True, help='full Git commit ID for the online updater and Studio UI')
    cli.add_argument('--suite-manifest-sha256', required=True, help='SHA-256 of the signed 2026.11.20 suite-version.json')
    cli.add_argument('output', type=Path)
    args = cli.parse_args()
    data, _ = bundle(args.ref, args.suite_manifest_sha256)
    if args.output.exists() and args.output.read_bytes()!=data:
        cli.error('output exists with different content')
    if not args.output.exists():
        with args.output.open('xb') as output:
            output.write(data)
    print(hashlib.sha256(data).hexdigest(), args.output)
