"""Factory integration must consume the same installer and pinned resources."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'software_components.py'
sys.path.insert(0,str(SOURCE.parent))


class SoftwareComponents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'rootfs'
        (self.root/'etc').mkdir(parents=True)
        self.resources = self.base/'resources'
        (self.resources/'asr-deps/vosk').mkdir(parents=True)
        (self.resources/'models/vosk-model-small-cn-0.22/am').mkdir(parents=True)
        (self.resources/'asr-deps/vosk/__init__.py').write_bytes(b'# vosk')
        (self.resources/'models/vosk-model-small-cn-0.22/am/final.mdl').write_bytes(b'model')
        files = [{'path':p.relative_to(self.resources).as_posix(),'size':p.stat().st_size,
                  'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(self.resources.rglob('*')) if p.is_file()]
        (self.resources/'resources-manifest.json').write_text(json.dumps(
            {'schema':1,'target':'linux-aarch64-cp313','model':{'name':'vosk-model-small-cn-0.22'},'files':files}))
        self.package,self.key,self.engine,self.bootstrap = [self.base/p for p in ('update.tar','release.pub','updaterd','bootstrap.py')]
        for path in (self.package,self.key,self.engine,self.bootstrap):
            path.write_text(path.name)
        self.calls=[]

    def module(self):
        self.assertTrue(SOURCE.is_file(), 'factory software component integration is missing')
        spec = importlib.util.spec_from_file_location('software_components',SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def install(self, runner=None):
        def record(command, **kwargs):
            self.calls.append(command)
            release = self.root/'opt/microduck/software/releases/2026.10.27'
            release.mkdir(parents=True)
            (release/'suite-version.json').write_text(json.dumps({'version':'2026.10.27','files':{}}))
            try:
                (release.parent.parent/'current').symlink_to('releases/2026.10.27',target_is_directory=True)
            except OSError:
                self.skipTest('installed suite requires directory symlinks')
            return None
        return self.module().install(self.root,self.package,self.key,self.engine,self.resources,
            bootstrap_script=self.bootstrap,engine_command=['qemu-aarch64','-L','/test-libs'],
            runner=runner or record)

    def test_factory_calls_shared_signed_bootstrap_and_writes_resource_provenance(self):
        result = self.install()
        self.assertEqual(len(self.calls),1)
        command = self.calls[0]
        for option,value in (('--root',str(self.root)),('--package',str(self.package)),
                             ('--public-key',str(self.key)),('--engine',str(self.engine))):
            self.assertEqual(command[command.index(option)+1],value)
        self.assertIn('--offline',command)
        self.assertEqual(json.loads(command[command.index('--engine-command-json')+1]),['qemu-aarch64','-L','/test-libs'])
        copied = self.root/'opt/microduck/resources'
        self.assertEqual((copied/'models/vosk-model-small-cn-0.22/am/final.mdl').read_bytes(),b'model')
        provenance = json.loads((self.root/'usr/share/doc/microduck/software-components.json').read_text())
        self.assertEqual(provenance['package_sha256'],hashlib.sha256(self.package.read_bytes()).hexdigest())
        self.assertEqual(result,provenance)

    def test_corrupt_resources_rejected_before_installer_or_target_write(self):
        (self.resources/'models/vosk-model-small-cn-0.22/am/final.mdl').write_text('corrupted')
        with self.assertRaisesRegex(ValueError,'hash'):
            self.install()
        self.assertEqual(self.calls,[])
        self.assertFalse((self.root/'opt').exists())

    def test_undeclared_or_escaping_resource_is_not_copied(self):
        (self.resources/'private.key').write_text('unlisted fixture')
        with self.assertRaisesRegex(ValueError,'unlisted'):
            self.install()
        self.assertEqual(self.calls,[])
        (self.resources/'private.key').unlink()
        manifest_path=self.resources/'resources-manifest.json'
        manifest=json.loads(manifest_path.read_text())
        manifest['files'][0]['path']='../../escape'
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'unsafe'):
            self.install()

    def test_bootstrap_failure_does_not_write_success_record(self):
        def fail(*args,**kwargs):
            raise RuntimeError('bootstrap failed')
        with self.assertRaisesRegex(RuntimeError,'bootstrap failed'):
            self.install(fail)
        self.assertFalse((self.root/'usr/share/doc/microduck/software-components.json').exists())


if __name__=='__main__':
    unittest.main()
