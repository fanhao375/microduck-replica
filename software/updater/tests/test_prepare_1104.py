"""Stock bridge host, import, verification and retry boundaries (Linux install fixtures)."""
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE / 'stock1104'),str(HERE)]
from build import blob, bundle, STOCK, UPDATER, STUDIO as STUDIO_DIR
from install import ASSETS, ENGINE, CONTROL, STUDIO, SYSTEMD, NAME, check_stock, dropins, live_idle, payload, stage
from launch import select

class StockBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = subprocess.check_output(['git','-C',str(HERE),'rev-parse','HEAD'], text=True).strip()
        cls.stable_metadata = json.dumps({'version':'2026.11.20','source_revision':cls.ref,
            'files':{'studio/run.py':hashlib.sha256(b'stable fixture').hexdigest()}}).encode()
        cls.stable_digest = hashlib.sha256(cls.stable_metadata).hexdigest()
        cls.archive, cls.manifest = bundle(cls.ref,cls.stable_digest)
        cls.files = payload(zipfile.ZipFile(io.BytesIO(cls.archive)))

    def test_owner_scopes_and_reproducible_build(self):
        self.assertEqual(bundle(self.ref,self.stable_digest)[0],self.archive)
        for name in ('settings-updates.js','updates-ui-state.js'):
            path='software/studio/web/'+name
            self.assertEqual(self.files[name],blob(self.ref,path))
            self.assertEqual(self.manifest['originals'][name], hashlib.sha256(blob(STOCK,path)).hexdigest())
        for name,path in [('control.py',UPDATER),('updates.py',STUDIO_DIR)]:
            self.assertEqual(self.files[name],blob(self.ref,path+name))
        with mock.patch.object(Path,'read_text',side_effect=AssertionError('checkout content used')):
            self.assertEqual(bundle(self.ref,self.stable_digest)[0],self.archive)
        with zipfile.ZipFile(io.BytesIO(self.archive)) as prepared:
            for filename,entry in [('launch.py','payload/launch.py'),('install.py','__main__.py')]:
                self.assertEqual(prepared.read(entry),blob(self.ref,UPDATER+'stock1104/'+filename))
        with self.assertRaises(ValueError):
            bundle('HEAD',self.stable_digest)
        with self.assertRaises(ValueError):
            bundle(self.ref,'unknown-digest')
        contents = dropins()
        self.assertEqual(set(contents), {ENGINE, CONTROL, STUDIO})
        self.assertIn('UMask=0022', contents[ENGINE])
        self.assertIn('health.py', contents[ENGINE])
        self.assertIn('control.py', contents[CONTROL])
        self.assertNotIn('robotd', ''.join(contents.values()))
        self.assertEqual(self.manifest['originals']['app.py'], 'ca4f0735d3624cc4cb4b13f85a682967a2c1fd5a4f9f904b676646d1bdb418be')
        self.assertEqual(self.manifest['originals']['updates.py'], 'b33a4953e23e3fe96a084b9be31f410d5551dae9003ff7a0fcc794155cbc312d')

    def test_stage_restore_and_partial_retries_only_affect_owned_dropins(self):
        if getattr(os,'geteuid',lambda:-1)()!=0:
            self.skipTest('root-owned staging fixture')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'usr/local/lib').mkdir(parents=True)
            base=root/SYSTEMD.lstrip('/')
            base.mkdir(parents=True)
            self.assertIn('可暂存',stage(root,self.files,mode='check'))
            first=base/(ENGINE+'.service.d')/NAME
            other=base/(CONTROL+'.service.d')/NAME
            first.parent.mkdir()
            stage(root,self.files,mode='prepare')
            stage(root,self.files,mode='prepare')
            self.assertIn('完整暂存',stage(root,self.files,mode='check'))
            other.unlink()
            other.write_text('foreign')
            with self.assertRaisesRegex(ValueError,'已有同名'):
                stage(root,self.files,mode='restore')
            self.assertTrue(first.exists())
            other.unlink()
            other.symlink_to(os.path.relpath(root/ASSETS.lstrip('/')/'active'/(CONTROL+'.conf'),other.parent))
            (first.parent/'vendor.conf').write_text('untouched')
            stage(root,self.files,mode='restore')
            self.assertFalse(first.exists())
            self.assertEqual((first.parent/'vendor.conf').read_text(),'untouched')
            self.assertTrue((root/ASSETS.lstrip('/')/'control.py').is_file())

    def test_accepts_factory_root_but_refuses_changed_files_and_retargeted_current(self):
        if getattr(os,'geteuid',lambda:-1)()!=0:
            self.skipTest('root-owned stock fixture')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            code=types.ModuleType('legacy_bootstrap')
            code.__file__=str(HERE/'bootstrap.py')
            exec(compile(blob(STOCK,UPDATER+'bootstrap.py'),code.__file__,'exec'),code.__dict__)
            def write(path,data):
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(data)
            for unit,text in code.UNITS.items():
                write(root/'etc/systemd/system'/unit,text.encode())
            write(root/'etc/systemd/system/microduck-studio.service.d/90-software-update.conf',code.STUDIO_OVERRIDE.encode())
            base=root/'opt/microduck/software'
            legacy=base/'releases/2026.11.4'
            meta={'version':'2026.11.4','source_revision':STOCK,'files':{}}
            for name,digest in self.manifest['originals'].items():
                if name in ('updates.py','app.py') or name.endswith('.js'):
                    location=('studio/web/' if name.endswith('.js') else 'studio/studio/')+name
                    data=blob(STOCK,'software/'+location)
                    write(legacy/location,data)
                    meta['files'][location]=digest
                else:
                    write(root/'usr/local/lib/microduck-updater'/name,blob(STOCK,UPDATER+name))
            write(legacy/'suite-version.json',json.dumps(meta).encode())
            (base/'current').symlink_to('releases/2026.11.4')
            check_stock(root,self.manifest)
            stage(root,self.files,mode='prepare')
            check_stock(root,self.manifest)
            assets=root/ASSETS.lstrip('/')
            studio=legacy/'studio'
            selected=select(studio,studio,assets)
            self.assertIsNotNone(selected)
            self.assertEqual(Path(selected.find_spec('studio.app',None).origin),studio/'studio/app.py')
            self.assertIsNone(selected.find_spec('studio.servo',None))
            self.assertIsNone(select(base/'releases/2026.11.20/studio',studio,assets))
            class OriginalStatic:
                def lookup_path(self,path): return ('stock/'+path,None)
            overlay=selected.static_class(OriginalStatic)()
            overlay.directory=str(studio/'web')
            for name in ('settings-updates.js','updates-ui-state.js'):
                self.assertEqual(Path(overlay.lookup_path(name)[0]).read_bytes(),self.files[name])
            self.assertEqual(overlay.lookup_path('app.js'),('stock/app.js',None))
            overlay.directory=str(base/'releases/2026.11.20/studio/web')
            self.assertEqual(overlay.lookup_path('settings-updates.js'),('stock/settings-updates.js',None))
            import install
            rc=base/'releases'/install.RC_VERSION
            write(rc/'studio/run.py',b'rc.1 fixture')
            rc_metadata=json.dumps({'version':install.RC_VERSION,'source_revision':install.RC_REVISION,
                'files':{'studio/run.py':hashlib.sha256(b'rc.1 fixture').hexdigest()}}).encode()
            write(rc/'suite-version.json',rc_metadata)
            (base/'current').unlink()
            (base/'current').symlink_to('releases/'+install.RC_VERSION)
            with self.assertRaisesRegex(ValueError,'rc.1.*摘要'):
                check_stock(root,self.manifest)
            with mock.patch.object(install,'RC_METADATA_SHA256',hashlib.sha256(rc_metadata).hexdigest()):
                self.assertEqual(check_stock(root,self.manifest),install.RC_VERSION)
                (rc/'studio/run.py').write_text('local edit')
                with self.assertRaisesRegex(ValueError,'rc.1 文件摘要'):
                    check_stock(root,self.manifest)
            self.assertIsNone(select(rc/'studio',studio,assets))
            (base/'current').unlink()
            stable=base/'releases/2026.11.20'
            write(stable/'studio/run.py',b'stable fixture')
            write(stable/'suite-version.json',self.stable_metadata)
            (base/'current').symlink_to('releases/2026.11.20')
            self.assertEqual(check_stock(root,self.manifest),'2026.11.20')
            legacy.rename(base/'retained-stock')
            with self.assertRaises(FileNotFoundError):
                check_stock(root,self.manifest)
            write(root/install.IMAGE_MANIFEST.lstrip('/'),b'factory fixture')
            with mock.patch.object(install,'IMAGE_MANIFEST_SHA256',hashlib.sha256(b'factory fixture').hexdigest()):
                self.assertEqual(check_stock(root,self.manifest),'2026.11.20')
                (stable/'studio/run.py').write_bytes(b'customized')
                with self.assertRaisesRegex(ValueError,'文件摘要'):
                    check_stock(root,self.manifest)
            (base/'retained-stock').rename(legacy)
            (base/'current').unlink()
            (base/'current').symlink_to('releases/2026.11.4')
            (studio/'studio/app.py').write_bytes(b'customized')
            with self.assertRaisesRegex(ValueError,'摘要'):
                check_stock(root,self.manifest)
            with self.assertRaisesRegex(RuntimeError,'mismatch'):
                select(studio,studio,assets)
            (base/'current').unlink()
            (base/'current').symlink_to('releases/custom')
            with self.assertRaisesRegex(ValueError,'原版'):
                check_stock(root,self.manifest)

    def test_busy_software_engine_native_or_model_fails_closed(self):
        from control import UpdateError
        good={'ok':True,'busy':False,'engine_available':True}
        for blocked in ('busy','engine','studio','model','unknown_model','marker','custom_command','v1','idle'):
            with self.subTest(blocked=blocked), ExitStack() as ctx:
                ctx.enter_context(mock.patch('install.os.path.lexists',return_value=blocked=='marker'))
                ctx.enter_context(mock.patch('control.json_socket',side_effect=[{**good,'busy':blocked=='busy'},{'ready':blocked!='studio'}]))
                engine=ctx.enter_context(mock.patch('control.Engine')).return_value
                state={'component':'software','phase':'applying' if blocked=='engine' else 'idle'}
                bus={} if blocked=='unknown_model' else {'policy_enabled':blocked=='model'}
                engine.call.side_effect=[[state],bus]
                import install
                line='argv[]=/usr/bin/python3 -I run.py '+install.STUDIO_ARGS+' ;'
                if blocked=='v1':
                    line=line.replace('run.py', install.LEGACY_ASSETS+'/launch.py')
                ctx.enter_context(mock.patch('install.subprocess.check_output',return_value='custom' if blocked=='custom_command' else line))
                if blocked in ('v1','idle'):
                    live_idle()
                else:
                    with self.assertRaises((ValueError,UpdateError)):
                        live_idle()

    def test_v1_adoption_preserves_effective_units_on_interruption(self):
        if getattr(os,'geteuid',lambda:-1)()!=0:
            self.skipTest('root-owned migration fixture')
        import install
        legacy_ref='5ea67f4b750efbedaa06804f85acaed85b3426ca'
        builder=types.ModuleType('v1_builder')
        builder.__file__=str(HERE/'stock1104/build.py')
        exec(blob(legacy_ref,UPDATER+'stock1104/build.py'),builder.__dict__)
        builder.zip_source=lambda name: blob(legacy_ref,UPDATER+'stock1104/'+name).rstrip(b'\n')+b'\n\n'
        archive=builder.bundle()[0]
        self.assertEqual(hashlib.sha256(archive).hexdigest(),'acf175a7ac2e08a9b0bb94347efd1a0a7257902e6958caf547bbf1f1789f1ccf')
        with tempfile.TemporaryDirectory() as temp, zipfile.ZipFile(io.BytesIO(archive)) as old:
            root=Path(temp)
            legacy=root/install.LEGACY_ASSETS.lstrip('/')
            legacy.mkdir(parents=True)
            for name in old.namelist():
                if name.startswith('payload/') or name=='manifest.json':
                    (legacy/Path(name).name).write_bytes(old.read(name))
            paths={}
            old_config=dropins(install.LEGACY_ASSETS)
            for unit,config in old_config.items():
                path=root/SYSTEMD.lstrip('/')/(unit+'.service.d')/NAME
                path.parent.mkdir(parents=True)
                path.write_text(config)
                paths[unit]=path
            # A changed legacy asset blocks adoption before any unit is touched.
            control=legacy/'control.py'
            control.write_text('foreign')
            with self.assertRaises(ValueError):
                stage(root,self.files,mode='prepare')
            control.write_bytes(old.read('payload/control.py'))
            self.assertFalse(any(p.is_symlink() for p in paths.values()))
            switch=install.switch_link
            def interrupt(link,value):
                if link.name=='active' and value=='online':
                    raise OSError('simulated interruption')
                switch(link,value)
            with mock.patch('install.switch_link',side_effect=interrupt):
                with self.assertRaisesRegex(OSError,'interruption'):
                    stage(root,self.files,mode='prepare')
            self.assertEqual({u:p.read_text() for u,p in paths.items()},old_config)
            stage(root,self.files,mode='prepare')
            self.assertEqual({u:p.read_text() for u,p in paths.items()},dropins())
            self.assertEqual(control.read_bytes(),old.read('payload/control.py'))

    @unittest.skipIf(sys.version_info < (3,12),'factory Studio requires Python 3.12+ syntax')
    def test_prepared_stock_imports_and_serves_the_two_assets_via_asgi(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            studio=base/'software/studio'
            archive=subprocess.check_output(['git','-C',str(HERE.parents[1]),'-c','core.autocrlf=false',
                                            'archive','--format=zip',STOCK,STUDIO_DIR])
            with zipfile.ZipFile(io.BytesIO(archive)) as original:
                original.extractall(base)
            web=studio/'web'
            web.mkdir()
            for name in ('settings-updates.js','updates-ui-state.js','app.js'):
                (web/name).write_bytes(blob(STOCK,'software/studio/web/'+name))
            (studio.parent/'suite-version.json').write_text(json.dumps(
                {'version':self.manifest['version'],'source_revision':STOCK}))
            assets=base/'assets'
            assets.mkdir()
            for name,data in self.files.items():
                (assets/name).write_bytes(data)
            # Import the archived launcher and stock app with the pinned Studio dependencies.
            # A separate interpreter prevents another test's studio package or ASGI version leaking in.
            script="""import asyncio, sys
from pathlib import Path
studio, assets, deps = map(Path, sys.argv[1:])
sys.path[:0] = [str(assets), str(deps), str(studio)]
import launch
sys.meta_path.insert(0, launch.select(studio, studio, assets))
from studio.app import AppStatic, RUNTIME_ROOT
from studio.updates import Updates
assert RUNTIME_ROOT == studio.parent
static = AppStatic(directory=str(studio/'web'))
async def request(name):
    messages=[]
    async def send(message): messages.append(message)
    scope={'type':'http','path':'/'+name,'method':'GET','headers':[]}
    await static(scope, None, send)
    assert messages[0]['status']==200
    assert (b'cache-control',b'no-cache') in messages[0]['headers']
    return b''.join(m.get('body', b'') for m in messages)
async def verify():
    for name in ('settings-updates.js','updates-ui-state.js','app.js'):
        expected=(studio/'web' if name=='app.js' else assets)/name
        assert await request(name)==expected.read_bytes()
asyncio.run(verify())
"""
            subprocess.run([sys.executable,'-I','-B','-c',script,str(studio),str(assets),
                            str(HERE.parent/'studio/deps')],check=True)
