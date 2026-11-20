"""Stock bridge host, import, verification and retry boundaries (Linux install fixtures)."""
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
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
        cls.archive, cls.manifest = bundle()
        cls.files = payload(zipfile.ZipFile(io.BytesIO(cls.archive)))

    def test_owner_scopes_and_reproducible_build(self):
        self.assertEqual(bundle()[0],self.archive)
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
            first.write_text(dropins()[ENGINE])
            stage(root,self.files,mode='prepare')
            stage(root,self.files,mode='prepare')
            self.assertIn('完整暂存',stage(root,self.files,mode='check'))
            other.write_text('foreign')
            with self.assertRaisesRegex(ValueError,'已有同名'):
                stage(root,self.files,mode='restore')
            self.assertTrue(first.exists())
            other.write_text(dropins()[CONTROL])
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
                if name in ('updates.py','app.py'):
                    location='studio/studio/'+name
                    data=blob(STOCK,STUDIO_DIR+name)
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
        for blocked in ('busy','engine','studio','model','unknown_model','marker','custom_command','idle'):
            with self.subTest(blocked=blocked), ExitStack() as ctx:
                ctx.enter_context(mock.patch('install.os.path.lexists',return_value=blocked=='marker'))
                ctx.enter_context(mock.patch('control.json_socket',side_effect=[{**good,'busy':blocked=='busy'},{'ready':blocked!='studio'}]))
                engine=ctx.enter_context(mock.patch('control.Engine')).return_value
                state={'component':'software','phase':'applying' if blocked=='engine' else 'idle'}
                bus={} if blocked=='unknown_model' else {'policy_enabled':blocked=='model'}
                engine.call.side_effect=[[state],bus]
                import install
                line='argv[]=/usr/bin/python3 -I run.py '+install.STUDIO_ARGS+' ;'
                ctx.enter_context(mock.patch('install.subprocess.check_output',return_value='custom' if blocked=='custom_command' else line))
                if blocked=='idle':
                    live_idle()
                else:
                    with self.assertRaises((ValueError,UpdateError)):
                        live_idle()
