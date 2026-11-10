"""Publisher injection is optional, fixed and part of signed suite provenance."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import build_package as B


class NativePackage(unittest.TestCase):
    def frozen(self,native_source=None,*,bundle_source=None,extra=None,voice_source=None,reader=True):
        if native_source is None:
            native_source=(Path(__file__).resolve().parents[2]/'studio/deploy/native_update.py').read_bytes()
        buffer=io.BytesIO()
        members={'software/studio/deploy/microduck-helper':b'helper','software/studio/deploy/native_update.py':native_source}
        if bundle_source is not None:members['software/studio/deploy/controller_bundle.py']=bundle_source
        if bundle_source is not None or voice_source is not None:
            members['software/duck-echo/duck_echo.py']=voice_source if voice_source is not None else b'PADD_MAPPING_CONTRACT = 1\n'
            if reader:members['software/duck-echo/pad_mapping.py']=b'# synthetic reader boundary\n'
        members.update(extra or {})
        with tarfile.open(fileobj=buffer,mode='w') as archive:
            for name,data in members.items():
                info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
        return buffer.getvalue()

    def build(self,native=None,*,native_source=None,padd=None,bundle_source=None,extra=None,voice_source=None,reader=True):
        self.assertIn('native_robotd',__import__('inspect').signature(B.build).parameters,'missing optional native publisher input')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'native';binary.write_bytes(native or b'')
            kwargs={}
            if padd is not None:
                self.assertIn('native_padd',__import__('inspect').signature(B.build).parameters,'missing explicit reviewed padd input')
                padd_file=root/'padd';padd_file.write_bytes(padd);kwargs['native_padd']=padd_file
            def run(argv,**kwargs):
                if argv[0]=='fake-zstd': kwargs['stdout'].write(kwargs['input'])
                if '-S' in argv: Path(argv[argv.index('-x')+1]).write_bytes(b'signature')
            with mock.patch.object(B.subprocess,'check_output',side_effect=['a'*40,self.frozen(native_source,bundle_source=bundle_source,extra=extra,voice_source=voice_source,reader=reader)]), mock.patch.object(B.subprocess,'run',side_effect=run), mock.patch.object(B.shutil,'which',return_value='fake-zstd'):
                B.build(root,'HEAD','2026.10.30',root,'secret','public',zstd='fake-zstd',native_robotd=binary if native else None,**kwargs)
            with tarfile.open(root/'2026.10.30.tar.zst') as archive:
                return {item.name:archive.extractfile(item).read() for item in archive}

    def test_no_native_input_retains_no_payload(self):
        files=self.build()
        self.assertNotIn('studio/deploy/native/robotd',files)
        self.assertNotIn('studio/deploy/native/manifest.json',files)

    def test_reviewed_actual_arm64_pie_is_injected_with_hash_bound_manifest(self):
        artifact=Path('F:/chengshenzhilu/Robot/Microduck/artifacts/image-rc-20261110/robotd-control50-aarch64')
        if not artifact.exists():
            artifact=Path('/mnt/f/chengshenzhilu/Robot/Microduck/artifacts/image-rc-20261110/robotd-control50-aarch64')
        if not artifact.exists(): self.skipTest('reviewed local release input not present')
        try:
            files=self.build(artifact.read_bytes())
        except ValueError as error:
            self.fail(str(error))
        suite=json.loads(files['suite-version.json']);manifest=json.loads(files['studio/deploy/native/manifest.json'])
        for name in ('studio/deploy/native/robotd','studio/deploy/native/manifest.json'):
            self.assertEqual(suite['files'][name],hashlib.sha256(files[name]).hexdigest())
        self.assertEqual(manifest['sha256'],'f8c720a3acf2eb5e6c844ba041d79d7312f49a50e99c04182c5ec0dc4bb9ac9e')
        self.assertEqual(manifest['source_revision'],'56046c5aa10be4de12d4389ae0463e4f129df940')
        self.assertEqual(manifest['schema'],'microduck-native-payload/1')
        self.assertEqual(manifest['build'],'replica-imu-home-control.50')
        self.assertEqual(manifest['official_version'],'0.15.1')
        self.assertEqual(manifest['architecture'],'aarch64')
        self.assertEqual(manifest['software_version'],'2026.10.30')
        self.assertEqual(manifest['size'],7065952)
        self.assertEqual(manifest['model_api'],2)
        self.assertEqual(manifest['ipc_api'],44)
        self.assertEqual(manifest['supported_installed_sha256'],[
            'edf182edb7bf11aeeeedfdab98170ab2ec48a9ec18417f9b68c744b0a60c6d16',
            'febf44e2e5d4e573f4513f25a1ae6480b976c3988dd3d5e7cdfcf84d2a2d5063',
            '3dbbf1723c0315afc15fd9f28787c0cfc194622a67802adcd1f5aa397ea47422',
            '725b74cd93660a312970a44879abe5b75e8becf108499a1b8798961e48ca5f00',
            'e5ff7fb429fd0dfb762eb4da1bcc5a26b48b6d2dfdae4bb4904213e37e022ed0'])

    def test_unreviewed_bytes_are_refused(self):
        with self.assertRaises(ValueError): self.build(b'foreign binary')

    def test_published47_is_a_predecessor_and_cannot_be_new_payload(self):
        for root in (Path('F:/chengshenzhilu/Robot/Microduck'),Path('/mnt/f/chengshenzhilu/Robot/Microduck')):
            artifact=root/'artifacts/imu-independent-20261030/robotd-independent-imu-arm64'
            if artifact.is_file():break
        else:self.skipTest('published predecessor release input not present')
        self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(),
            '725b74cd93660a312970a44879abe5b75e8becf108499a1b8798961e48ca5f00')
        with self.assertRaises(ValueError):self.build(artifact.read_bytes())

    def published_padd(self):
        relative='artifacts/native-release-prep-20261030/padd-arm64-a'
        for root in (Path('F:/chengshenzhilu/Robot/Microduck'),Path('/mnt/f/chengshenzhilu/Robot/Microduck')):
            if (root/relative).is_file():return (root/relative).read_bytes()
        self.skipTest('published padd release input not present')

    def test_real_frozen_pair_is_injected_with_consistent_api44_and_unchanged_padd(self):
        bundle_source=(Path(__file__).resolve().parents[2]/'studio/deploy/controller_bundle.py').read_bytes()
        files=self.build(self.reviewed_binary(),padd=self.published_padd(),bundle_source=bundle_source)
        native=json.loads(files['studio/deploy/native/manifest.json'])
        bundle=json.loads(files['studio/deploy/native/controller-manifest.json'])
        for field in ('sha256','size','source_revision','build','ipc_api'):
            self.assertEqual(bundle['components']['robotd'][field],native[field])
        self.assertEqual(native['ipc_api'],44)
        self.assertEqual(bundle['components']['padd']['sha256'],
            'dc1c7043a108964583fc09a11e3d6f87a25e259c346f7ce9154e77500a0881ec')
        self.assertEqual(files['studio/deploy/native/padd'],self.published_padd())

    def test_real_frozen_pair_rejects_tampered_robotd_or_padd_bytes(self):
        bundle_source=(Path(__file__).resolve().parents[2]/'studio/deploy/controller_bundle.py').read_bytes()
        robot,pad=self.reviewed_binary(),self.published_padd()
        for robot_bytes,pad_bytes in ((robot[:-1]+bytes([robot[-1]^1]),pad),
                                      (robot,pad[:-1]+bytes([pad[-1]^1]))):
            with self.subTest(component='robotd' if robot_bytes!=robot else 'padd'),self.assertRaises(ValueError):
                self.build(robot_bytes,padd=pad_bytes,bundle_source=bundle_source)

    def reviewed_binary(self):
        relative='artifacts/image-rc-20261110/robotd-control50-aarch64'
        for root in (Path('F:/chengshenzhilu/Robot/Microduck'),Path('/mnt/f/chengshenzhilu/Robot/Microduck')):
            if (root/relative).is_file():return (root/relative).read_bytes()
        self.skipTest('reviewed local release input not present')

    def test_native_manifest_comes_from_frozen_revision_not_builder_worktree(self):
        source=(Path(__file__).resolve().parents[2]/'studio/deploy/native_update.py').read_bytes()
        altered=source.replace(b"SOURCE = '56046c5aa10be4de12d4389ae0463e4f129df940'",b"SOURCE = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'")
        self.assertNotEqual(source,altered)
        files=self.build(self.reviewed_binary(),native_source=altered)
        self.assertEqual(json.loads(files['studio/deploy/native/manifest.json'])['source_revision'],'b'*40)

    def test_frozen_validator_cannot_be_bypassed_by_valid_builder_worktree(self):
        source=(Path(__file__).resolve().parents[2]/'studio/deploy/native_update.py').read_bytes()
        altered=source.replace(b"REVIEWED_SHA256 = 'f8c720a3acf2eb5e6c844ba041d79d7312f49a50e99c04182c5ec0dc4bb9ac9e'",b"REVIEWED_SHA256 = '1111111111111111111111111111111111111111111111111111111111111111'")
        self.assertNotEqual(source,altered)
        with self.assertRaises(ValueError):self.build(self.reviewed_binary(),native_source=altered)

    def bundle_fixture(self):
        # Synthetic bytes test the publisher boundary, not production ELF acceptance.
        padd=b'publisher-contract-fixture'
        source=f'''import hashlib
def bundle_manifest(version,robotd,padd):
    return {{'schema':'synthetic-controller-contract/1','software_version':version,
             'padd_sha256':hashlib.sha256(padd).hexdigest(),
             'robotd_sha256':hashlib.sha256(robotd).hexdigest(),'frozen_marker':'fixture-only'}}
def validate_bundle(robotd,padd,manifest,version):
    if padd!={padd!r} or hashlib.sha256(robotd).hexdigest()!='f8c720a3acf2eb5e6c844ba041d79d7312f49a50e99c04182c5ec0dc4bb9ac9e' or manifest!=bundle_manifest(version,robotd,padd):
        raise ValueError('not the reviewed fixture inputs')
'''.encode()
        return padd,source

    def test_companion_requires_matching_explicit_robotd_input(self):
        padd,source=self.bundle_fixture()
        with self.assertRaises(ValueError):self.build(padd=padd,bundle_source=source)

    def test_companion_and_manifest_are_bound_to_signed_suite(self):
        padd,source=self.bundle_fixture()
        files=self.build(self.reviewed_binary(),padd=padd,bundle_source=source)
        manifest=json.loads(files['studio/deploy/native/controller-manifest.json'])
        suite=json.loads(files['suite-version.json'])
        self.assertEqual(manifest['frozen_marker'],'fixture-only')
        self.assertEqual(suite.get('contracts'),{'padd_mapping':1})
        self.assertEqual(files['studio/deploy/native/padd'],padd)
        for name in ('robotd','manifest.json','padd','controller-manifest.json'):
            path='studio/deploy/native/'+name
            self.assertEqual(suite['files'][path],hashlib.sha256(files[path]).hexdigest())

    def test_unreviewed_companion_is_refused_by_frozen_validator(self):
        padd,source=self.bundle_fixture()
        with self.assertRaises(ValueError):self.build(self.reviewed_binary(),padd=padd+b'tamper',bundle_source=source)

    def test_companion_cannot_be_injected_by_git_archive_without_explicit_input(self):
        with self.assertRaises(ValueError):
            self.build(extra={'software/studio/deploy/native/padd':b'foreign'})

    def test_new_voice_contract_survives_future_software_only_update(self):
        files=self.build(voice_source=b'PADD_MAPPING_CONTRACT = 1\n')
        self.assertEqual(json.loads(files['suite-version.json']).get('contracts'),{'padd_mapping':1})

    def test_companion_requires_explicit_voice_contract_and_reader(self):
        padd,source=self.bundle_fixture()
        for kwargs in ({'voice_source':b'# legacy voice\n'},{'reader':False}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                self.build(self.reviewed_binary(),padd=padd,bundle_source=source,**kwargs)

    def test_voice_contract_is_strict_and_never_executes_voice_module(self):
        files=self.build(voice_source=b'PADD_MAPPING_CONTRACT = 1\nraise RuntimeError("must not execute voice")\n')
        self.assertEqual(json.loads(files['suite-version.json']).get('contracts'),{'padd_mapping':1})
        for declaration in (b'True',b'2',b'get_contract()'):
            with self.subTest(value=declaration),self.assertRaises(ValueError):
                self.build(voice_source=b'PADD_MAPPING_CONTRACT = '+declaration+b'\n')

    def test_nested_legacy_voice_cannot_replace_declared_root_consumer(self):
        voice=b'PADD_MAPPING_CONTRACT = 1\n'
        files=self.build(voice_source=voice,extra={
            'software/duck-echo/legacy/duck_echo.py':b'LEGACY = True\n',
            'software/duck-echo/legacy/pad_mapping.py':b'# legacy reader\n'})
        self.assertEqual(files['duck-echo/duck_echo.py'],voice)
        self.assertEqual(files['duck-echo/pad_mapping.py'],b'# synthetic reader boundary\n')

    def test_duplicate_runtime_target_is_refused_before_signing(self):
        buffer=io.BytesIO(self.frozen(extra={'software/studio/web/app.js':b'first'}))
        with tarfile.open(fileobj=buffer,mode='a') as archive:
            info=tarfile.TarInfo('software/studio/web/app.js');info.size=6
            archive.addfile(info,io.BytesIO(b'second'))
        with mock.patch.object(self,'frozen',return_value=buffer.getvalue()),self.assertRaises(ValueError):
            self.build()


if __name__=='__main__':unittest.main()
