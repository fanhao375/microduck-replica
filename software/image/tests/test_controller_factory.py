"""The offline factory companion comes only from the verified signed suite."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest

HERE = Path(__file__).resolve().parents[1]
SOURCE = HERE/'controller_factory.py'
DEPLOY = HERE.parent/'studio/deploy'
VERSION = '2026.11.4'
ROBOT = b'\x7fELF\x02\x01\x01'+b'\0'*9+b'\x03\0\xb7\0'+b'factory robot fixture'*8
PAD = b'\x7fELF\x02\x01\x01'+b'\0'*9+b'\x03\0\xb7\0'+b'factory padd fixture'*8
sha = lambda data: hashlib.sha256(data).hexdigest()
sys.path.insert(0,str(HERE))


class ControllerFactory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'rootfs'
        (self.root/'etc').mkdir(parents=True)
        self.release = self.root/'opt/microduck/software/releases'/VERSION
        self.release.mkdir(parents=True)
        try:
            (self.release.parent.parent/'current').symlink_to('releases/'+VERSION, target_is_directory=True)
        except OSError:
            self.skipTest('installed suite requires directory symlinks')
        self.files = {}
        self.suite = {'version':VERSION, 'files':{}, 'contracts':{'padd_mapping':1}}

    def module(self):
        self.assertTrue(SOURCE.is_file(), 'offline controller factory integration is missing')
        spec = importlib.util.spec_from_file_location('controller_factory', SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def write(self, name, data, *, listed=True):
        path = self.release/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.files[name] = data
        if listed:
            self.suite['files'][name] = sha(data)
        self.metadata()

    def metadata(self):
        (self.release/'suite-version.json').write_text(json.dumps(self.suite), encoding='utf-8')

    def pair(self, *, suffix=b''):
        # Only these fixture-specific hash pins change. Production schemas,
        # manifests, validation, units and immutable gate bytes remain real.
        native = DEPLOY.joinpath('native_update.py').read_bytes()+(
            '\n# Synthetic factory test pins only.\nREVIEWED_SHA256 = '+repr(sha(ROBOT))+
            '\nREVIEWED_SIZE = '+repr(len(ROBOT))+'\n').encode()
        bundle = DEPLOY.joinpath('controller_bundle.py').read_bytes()+(
            '\n# Synthetic factory test pins only.\nROBOTD_SHA256 = '+repr(sha(ROBOT))+
            '\nROBOTD_SIZE = '+repr(len(ROBOT))+'\nPADD_SHA256 = '+repr(sha(PAD))+
            '\nPADD_SIZE = '+repr(len(PAD))+'\n').encode()+suffix
        n, c = types.ModuleType('fixture_native'), types.ModuleType('fixture_bundle')
        exec(compile(bundle, 'fixture_bundle', 'exec'), c.__dict__)
        # Keep this synthetic fixture coherent while release pins are being
        # updated independently; production vector consistency has its own tests.
        ipc = c.bundle_manifest(VERSION,ROBOT,PAD)['components']['robotd']['ipc_api']
        native += ('\nIPC_API = '+repr(ipc)+'\n').encode()
        exec(compile(native, 'fixture_native', 'exec'), n.__dict__)
        for name, data in {
            'studio/deploy/native_update.py':native,
            'studio/deploy/controller_bundle.py':bundle,
            'studio/deploy/native/robotd':ROBOT,
            'studio/deploy/native/padd':PAD,
            'studio/deploy/native/manifest.json':json.dumps(n.native_manifest(VERSION,ROBOT)).encode(),
            'studio/deploy/native/controller-manifest.json':json.dumps(c.bundle_manifest(VERSION,ROBOT,PAD)).encode(),
            'duck-echo/duck_echo.py':b'PADD_MAPPING_CONTRACT = 1\n',
            'duck-echo/pad_mapping.py':b'# signed mapping reader fixture\n',
        }.items():
            self.write(name,data)
        for name, data in ((c.ROBOTD,ROBOT),(c.PADD,PAD)):
            path = self.root/name.lstrip('/')
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o755)
        self.c = c
        return c

    def install(self):
        return self.module().install(self.root)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix():(p.read_bytes(),stat.S_IMODE(p.stat().st_mode))
                for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}

    def require_root(self):
        if os.name=='posix' and os.geteuid()!=0:
            self.skipTest('factory ownership verification requires root')

    def software_install(self, runner):
        from software_components import install
        resources = self.base/'resources'
        members = {'asr-deps/vosk/__init__.py':b'# vosk',
                   'models/vosk-model-small-cn-0.22/am/final.mdl':b'model'}
        for name,data in members.items():
            path = resources/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data)
        (resources/'resources-manifest.json').write_text(json.dumps(
            {'schema':1,'target':'linux-aarch64-cp313','files':[
                {'path':name,'size':len(data),'sha256':sha(data)} for name,data in members.items()]}))
        inputs = [self.base/name for name in ('package','public-key','engine','bootstrap')]
        for path in inputs:
            path.write_bytes(b'input')
        return install(self.root,*inputs[:3],resources,bootstrap_script=inputs[3],runner=runner)

    def test_component_hook_installs_support_only_after_verified_bootstrap_returns(self):
        self.require_root()
        calls = []
        def bootstrap(command,**kwargs):
            calls.append(command)
            c = self.pair()
            self.assertFalse((self.root/c.COMPAT.lstrip('/')).exists())
        result = self.software_install(bootstrap)
        self.assertEqual(len(calls),1)
        self.assertTrue((self.root/self.c.COMPAT.lstrip('/')).is_file())
        self.assertEqual(result['controller_bundle']['version'],VERSION)
        provenance = json.loads((self.root/'usr/share/doc/microduck/software-components.json').read_bytes())
        self.assertEqual(provenance,result)

    def test_component_hook_rejects_partial_pair_without_success_provenance(self):
        def bootstrap(*args,**kwargs):
            self.pair()
            (self.release/'studio/deploy/native/padd').unlink()
        with self.assertRaises(ValueError):
            self.software_install(bootstrap)
        self.assertFalse((self.root/'usr/share/doc/microduck/software-components.json').exists())

    def test_old_suite_without_declared_pair_keeps_factory_behavior(self):
        self.metadata()
        self.write('studio/deploy/controller_bundle.py',DEPLOY.joinpath('controller_bundle.py').read_bytes())
        before = self.snapshot()
        self.assertIsNone(self.install())
        self.assertEqual(self.snapshot(),before)

    def test_old_suite_without_pair_keeps_existing_permission_behavior(self):
        self.metadata()
        (self.root/'etc').chmod(0o777)
        self.assertIsNone(self.install())

    def test_fresh_factory_root_gets_exact_declared_units_guard_and_marker(self):
        self.require_root()
        c = self.pair()
        config = self.root/'etc/robot/feetech-ft5/robotd.toml'
        config.parent.mkdir(parents=True)
        config.write_bytes(b'output_protocol = "legacy"\ncommissioning = true\n')
        calibration = config.with_name('hd1910-calibration.toml')
        calibration.write_bytes(b'calibrated = false\n')
        before = self.snapshot()
        result = self.install()
        expected = {**c.UNITS,c.VOICE_GUARD:c.VOICE_GUARD_PROGRAM,c.COMPAT:c.COMPAT_BYTES}
        self.assertEqual(set(self.snapshot())-set(before),{p.lstrip('/') for p in expected})
        for name,data in expected.items():
            with self.subTest(path=name):
                path = self.root/name.lstrip('/')
                self.assertEqual(path.read_bytes(),data)
                if os.name=='posix':
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o644)
                    self.assertEqual((path.stat().st_uid,path.stat().st_gid),(0,0))
                    for parent in path.parents:
                        if parent==self.root:
                            break
                        self.assertEqual(parent.stat().st_uid,0)
                        self.assertEqual(parent.stat().st_mode&0o022,0)
        for name,value in before.items():
            self.assertEqual(self.snapshot()[name],value)
        self.assertEqual(result['schema'],c.PAYLOAD_SCHEMA)
        self.assertEqual(result['version'],VERSION)
        self.assertEqual(result['components'],{'robotd':sha(ROBOT),'padd':sha(PAD)})
        self.assertEqual(result['files'],{p:sha(data) for p,data in expected.items()})
        self.assertEqual(self.install(),result)

    def test_actual_image_pair_must_match_verified_payload_before_writes(self):
        c = self.pair()
        for name,original in ((c.ROBOTD,ROBOT),(c.PADD,PAD)):
            with self.subTest(path=name):
                target = self.root/name.lstrip('/')
                target.write_bytes(original+b'wrong image payload')
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError,'payload|component|match'):
                    self.install()
                self.assertEqual(self.snapshot(),before)
                target.write_bytes(original)

    def test_missing_image_binary_rejects_before_support_writes(self):
        c = self.pair()
        for name,original in ((c.ROBOTD,ROBOT),(c.PADD,PAD)):
            with self.subTest(path=name):
                path = self.root/name.lstrip('/')
                path.unlink()
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError,'missing|component'):
                    self.install()
                self.assertEqual(self.snapshot(),before)
                path.write_bytes(original)

    def test_each_missing_signed_pair_dependency_rejects_before_writes(self):
        self.pair()
        for name,data in self.files.items():
            with self.subTest(path=name):
                path = self.release/name
                path.unlink()
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.install()
                self.assertEqual(self.snapshot(),before)
                path.write_bytes(data)

    def test_signed_manifest_and_frozen_validator_reject_wrong_pair_bytes(self):
        self.pair()
        for name in ('studio/deploy/native/robotd','studio/deploy/native/padd'):
            with self.subTest(path=name):
                original = self.files[name]
                self.write(name,original+b'wrong signed input')
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError,'reviewed|vector|payload|component'):
                    self.install()
                self.assertEqual(self.snapshot(),before)
                self.write(name,original)

    def test_pair_and_legacy_manifests_both_validate_before_writes(self):
        self.pair()
        for name in ('studio/deploy/native/controller-manifest.json','studio/deploy/native/manifest.json'):
            with self.subTest(path=name):
                original = self.files[name]
                self.write(name,b'{}')
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.install()
                self.assertEqual(self.snapshot(),before)
                self.write(name,original)

    def test_all_dependency_hashes_are_checked_before_compiling_frozen_code(self):
        self.pair()
        marker = self.base/'unverified-code-ran'
        self.write('studio/deploy/controller_bundle.py',self.files['studio/deploy/controller_bundle.py']+
                   ('\nopen('+repr(str(marker))+',"w").write("bad")\n').encode())
        # Hash mismatch comes later than the module in the input list.
        (self.release/'duck-echo/pad_mapping.py').write_bytes(b'tampered reader')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError,'hash'):
            self.install()
        self.assertFalse(marker.exists())
        self.assertEqual(self.snapshot(),before)

    def test_missing_declared_pair_cannot_be_hidden_by_removing_files(self):
        self.pair()
        for name in ('studio/deploy/native/padd','studio/deploy/native/controller-manifest.json'):
            (self.release/name).unlink()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.snapshot(),before)

    def test_unlisted_pair_member_cannot_silently_fall_back(self):
        self.metadata()
        self.write('studio/deploy/native/padd',PAD,listed=False)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.snapshot(),before)

    def test_missing_voice_mapping_contract_rejects_before_writes(self):
        self.pair()
        for contract in (None,True,2):
            with self.subTest(contract=contract):
                self.suite['contracts'] = {'padd_mapping':contract}
                self.metadata()
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError,'voice|contract'):
                    self.install()
                self.assertEqual(self.snapshot(),before)

    def test_malformed_voice_contract_object_rejects_before_writes(self):
        self.pair()
        self.suite['contracts'] = ['padd_mapping']
        self.metadata()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError,'voice|contract'):
            self.install()
        self.assertEqual(self.snapshot(),before)

    def test_installed_startup_gate_requires_the_signed_mapping_aware_voice(self):
        self.require_root()
        c = self.pair()
        self.install()
        gate = types.ModuleType('installed_voice_gate')
        exec(compile((self.root/c.VOICE_GUARD.lstrip('/')).read_bytes(),'installed_voice_gate','exec'),gate.__dict__)
        self.assertEqual(gate.guard(str(self.root)),0)
        self.suite['contracts'] = {}
        self.metadata()
        self.assertEqual(gate.guard(str(self.root)),1)
        self.suite['contracts'] = {'padd_mapping':1}
        self.metadata()
        (self.release/'duck-echo/pad_mapping.py').write_bytes(b'changed reader')
        self.assertEqual(gate.guard(str(self.root)),1)

    def test_late_symlink_target_rejects_before_any_companion_file_write(self):
        c = self.pair()
        outside = self.base/'outside'
        outside.mkdir()
        target = self.root/c.COMPAT.lstrip('/')
        target.parent.parent.mkdir(parents=True,exist_ok=True)
        target.parent.symlink_to(outside,target_is_directory=True)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError,'symlink|unsafe'):
            self.install()
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(list(outside.iterdir()),[])

    def test_current_release_escape_and_live_root_are_refused(self):
        self.pair()
        current = self.release.parent.parent/'current'
        current.unlink()
        current.symlink_to(self.base,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'release|unsafe'):
            self.install()
        with self.assertRaisesRegex(ValueError,'offline'):
            self.module().install(Path(Path.cwd().anchor))

    def test_frozen_declaration_cannot_choose_escaping_support_paths(self):
        self.pair(suffix=b"\nCOMPAT = '/var/lib/../../outside-compat.json'\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError,'path|declaration|unsafe'):
            self.install()
        self.assertEqual(self.snapshot(),before)


if __name__=='__main__':
    unittest.main()
