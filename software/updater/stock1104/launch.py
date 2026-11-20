"""Stock-only Studio readiness compatibility. systemd runs this as the Studio user."""
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys

STOCK = Path('/opt/microduck/software/releases/2026.11.4/studio')
ASSETS = Path(__file__).resolve().parent

class LegacyModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, studio, assets):
        self.studio, self.assets = studio, assets

    def find_spec(self, fullname, path, target=None):
        if fullname in ('studio.app', 'studio.updates'):
            original = self.studio / 'studio' / (fullname.split('.')[1] + '.py')
            return importlib.util.spec_from_file_location(fullname, original, loader=self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        original = Path(module.__file__)
        # Compile with the real release filename so /healthz retains the original
        # runtime root. Never write a bytecode cache into the release tree.
        exec(compile((self.assets / original.name).read_bytes(), str(original), 'exec'), module.__dict__)

def select(studio, stock, assets):
    if studio != stock:
        return None
    manifest = json.loads((assets / 'manifest.json').read_bytes())
    version = json.loads((studio.parent / 'suite-version.json').read_bytes())
    if version.get('source_revision') != manifest['stock'] or version.get('version') != manifest['version']:
        return None
    for name in ('app.py','updates.py'):
        if hashlib.sha256((studio/'studio'/name).read_bytes()).hexdigest() != manifest['originals'][name]:
            raise RuntimeError('Stock Studio source mismatch: ' + name)
    return LegacyModules(studio, assets)

if __name__ == '__main__':
    studio = Path.cwd().resolve()
    override = select(studio, STOCK, ASSETS)
    if override is None:
        os.execv(sys.executable, [sys.executable, '-I', 'run.py', *sys.argv[1:]])
    sys.meta_path.insert(0, override)
    runpy.run_path(str(studio/'run.py'), run_name='__main__')
