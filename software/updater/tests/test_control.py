import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import os
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from control import Controller, UpdateError, preflight, choose_release,atomic_json
import test_packages

class Engine:
    def __init__(self): self.calls=[]; self.wait=None
    def call(self,method,params=None,progress=None):
        self.calls.append((method,params))
        if method=='update.status': return [{'component':'software','installed':'2026.10.26','phase':'idle'}]
        if method=='update.listInstalled': return [{'version':'2026.10.26','active':True}]
        if self.wait: self.wait.wait(2)
        return {'outcome':'dry_run_passed' if params.get('options',{}).get('dry_run') else 'applied','candidate':'2026.10.27','to':'2026.10.27'}

class Control(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','Linux advisory lock contract')
    def test_preflight_waits_for_prior_voice_lease_and_excludes_new_voice(self):
        import fcntl,io
        with tempfile.TemporaryDirectory() as tmp:
            entered=threading.Event();controller=self.make(tmp,preflight=entered.set)
            lock=Path(tmp)/'motion.lock';lock.touch()
            with lock.open('rb') as voice:
                fcntl.flock(voice,fcntl.LOCK_SH)
                controller.upload(io.BytesIO(test_packages.Packages().outer()))
                time.sleep(.1)
                self.assertFalse(entered.is_set(),'preflight raced an already dispatched voice action')
                self.assertTrue(controller.marker.exists())
                fcntl.flock(voice,fcntl.LOCK_UN)
            result=self.finish(controller);self.assertTrue(entered.is_set());self.assertTrue(result['ready'])

    def probe(self, bus, *, free=1024**3, studio=None):
        with tempfile.TemporaryDirectory() as tmp:
            config = {'marker': Path(tmp) / 'installing', 'state_dir': tmp,
                      'robot_socket': 'robot', 'studio_socket': 'studio'}
            def socket_reply(path, request):
                self.assertEqual(path, 'studio', 'software preflight must not depend on audio mode')
                return studio(request['op']) if studio else {'ready': True}
            with (mock.patch('control.Engine') as engine,
                  mock.patch('control.json_socket', side_effect=socket_reply),
                  mock.patch('control.shutil.disk_usage', return_value=mock.Mock(free=free)),
                  mock.patch('health.identities', return_value={})):
                rpc = engine.return_value.call
                if isinstance(bus, Exception):
                    rpc.side_effect = bus
                else:
                    rpc.return_value = bus
                preflight(config)
                rpc.assert_called_once_with('robot.busStatus')

    def test_software_only_needs_a_confirmed_inactive_policy(self):
        for bus in ({'policy_enabled':False,'homed':True},
                    {'policy_enabled':False,'homed':False,'native':{'torque_state_confirmed':False}},
                    {'policy_enabled':False,'all_joints_fresh':False,'native':{'joints':[{'velocity_rad_s':.4}]}},
                    {'mode':'commissioning','policy_motion_enabled':False}):
            with self.subTest(bus=bus): self.probe(bus)

    def test_active_ambiguous_or_unreachable_policy_refuses(self):
        for bus in ({'policy_enabled':True},{'policy_enabled':False,'policy_motion_enabled':True},
                    {},None,{'policy_enabled':0},{'policy_enabled':'false'},{'mode':'motion','policy_motion_enabled':False},
                    {'mode':'commissioning','policy_enabled':None,'policy_motion_enabled':False}, ConnectionRefusedError()):
            with self.subTest(bus=bus), self.assertRaises(UpdateError) as error: self.probe(bus)
            self.assertEqual(error.exception.code,'preflight')

    def test_legacy_studio_and_transaction_guards(self):
        calls=[]
        def old_studio(op):
            calls.append(op)
            return {'ready':True} if op=='readiness' else {'ready':False,'error':'不支持的内部请求'}
        self.probe({'policy_enabled':False},studio=old_studio)
        self.assertEqual(calls,['software-readiness','readiness'])
        def locked(op):
            self.assertEqual(op,'software-readiness')
            return {'ready':False,'error':'其他更新正在进行'}
        with self.assertRaisesRegex(UpdateError,'其他更新'): self.probe({'policy_enabled':False},studio=locked)
        with self.assertRaisesRegex(UpdateError,'512 MiB'): self.probe({'policy_enabled':False},free=1)

    def test_catalog_only_formal_fixed_repo_assets(self):
        release={'tag_name':'microduck-software-v2026.10.27','draft':False,'prerelease':False,
            'body':'local speech','assets':[{'name':'2026.10.27.software-update.tar','size':2000,
            'browser_download_url':'https://github.com/fanhao375/microduck-replica/releases/download/microduck-software-v2026.10.27/2026.10.27.software-update.tar'}]}
        self.assertEqual(choose_release([release])['version'],'2026.10.27')
        for field,value in (('draft',True),('prerelease',True),('tag_name','image-v2026.10.27')):
            wrong=copy.deepcopy(release); wrong[field]=value
            self.assertIsNone(choose_release([wrong]))
        wrong=copy.deepcopy(release); wrong['assets'][0]['browser_download_url']='https://evil.example/package'
        with self.assertRaises(UpdateError): choose_release([wrong])

    def make(self,tmp,engine=None,preflight=lambda:None):
        return Controller(Path(tmp),engine or Engine(),preflight=preflight,catalog=lambda:[])

    def finish(self,controller):
        for _ in range(200):
            if not controller.busy: return controller.view()
            time.sleep(.01)
        self.fail('worker did not finish')

    def test_downloaded_not_ready_when_robot_preflight_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            def refuse(): raise UpdateError('preflight','robot unknown')
            engine=Engine(); controller=self.make(tmp,engine,refuse)
            import io
            controller.upload(io.BytesIO(test_packages.Packages().outer()))
            view=self.finish(controller)
            self.assertFalse(view['ready']); self.assertEqual(view['job']['phase'],'waiting_validation')
            self.assertFalse(any(m=='update.apply' for m,_ in engine.calls))

    def test_model_retakes_control_after_validation_and_operator_can_retry(self):
        import io
        with tempfile.TemporaryDirectory() as tmp:
            bus = {'policy_enabled': False}
            engine = Engine()
            controller = self.make(tmp, engine, lambda: self.probe(bus))
            controller.upload(io.BytesIO(test_packages.Packages().outer()))
            self.assertTrue(self.finish(controller)['ready'])
            bus['policy_enabled'] = True
            controller.start('install')
            blocked = self.finish(controller)
            self.assertFalse(blocked['ready'])
            self.assertEqual(blocked['job']['phase'], 'waiting_validation')
            self.assertIn('模型正在接管', blocked['job']['error'])
            attempts = [p for m,p in engine.calls if m == 'update.apply']
            self.assertEqual(len(attempts), 1)
            self.assertTrue(attempts[0]['options']['dry_run'])
            bus['policy_enabled'] = False
            controller.start('validate')
            self.assertTrue(self.finish(controller)['ready'])
            controller.start('install')
            self.assertEqual(self.finish(controller)['job']['phase'], 'completed')

    def test_engine_validation_pinned_cache_revalidated_apply_and_one_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            import io
            engine=Engine(); controller=self.make(tmp,engine)
            controller.upload(io.BytesIO(test_packages.Packages().outer())); self.finish(controller)
            self.assertTrue(controller.view()['ready'])
            engine.wait=threading.Event()
            controller.start('install')
            with self.assertRaises(UpdateError): controller.start('check')
            self.assertTrue(controller.view()['busy'])
            engine.wait.set(); view=self.finish(controller)
            self.assertEqual(view['job']['phase'],'completed')
            calls=[p for m,p in engine.calls if m=='update.apply']
            self.assertTrue(calls[0]['options']['dry_run']); self.assertNotIn('dry_run',calls[-1]['options'])
            self.assertEqual(calls[0]['options']['from_dir'],calls[-1]['options']['from_dir'])
            self.assertEqual(calls[-1]['target'],{'exact':'2026.10.27'})

    def test_cache_tamper_and_restart_never_claim_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            import io
            controller=self.make(tmp); controller.upload(io.BytesIO(test_packages.Packages().outer())); self.finish(controller)
            (Path(tmp)/'cache'/'2026.10.27'/'2026.10.27.tar.gz').write_bytes(b'tampered')
            controller.start('install'); view=self.finish(controller)
            self.assertEqual(view['job']['phase'],'failed')
            self.assertFalse(view['ready'])
            restarted=self.make(tmp)
            self.assertFalse(restarted.view()['ready'])

    def test_controller_restart_reconciles_real_engine_attempt_without_reapplying(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Recovering(Engine):
                finished=False
                def call(self,method,params=None,progress=None):
                    if method=='update.status':return [{'component':'software','installed':'2026.10.26',
                        'phase':'idle' if self.finished else 'health_gate','last_attempt':{'at':int(time.time()),
                        'outcome':{'kind':'rolled_back','reason':'health failed'},'to':'2026.10.27'}}]
                    return super().call(method,params,progress)
            atomic_json(Path(tmp)/'job.json',{'id':'persistent','operation':'install','phase':'health_gate','started_at':time.time()-1})
            engine=Recovering();controller=self.make(tmp,engine)
            self.assertTrue(controller.busy)
            for _ in range(100):
                if controller.marker.exists():break
                time.sleep(.005)
            self.assertTrue(controller.marker.exists())
            engine.finished=True;result=self.finish(controller)
            self.assertEqual(result['job']['id'],'persistent');self.assertEqual(result['job']['phase'],'rolled_back')
            self.assertFalse(controller.marker.exists())
            self.assertFalse(any(method=='update.apply' for method,_ in engine.calls))

    def test_transport_failure_during_apply_retains_gate_until_durable_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            import io
            class Lost(Engine):
                lost=False
                def call(self,method,params=None,progress=None):
                    if self.lost and method=='update.status':return [{'component':'software','installed':'2026.10.27',
                        'phase':'idle','last_attempt':{'at':int(time.time()),'to':'2026.10.27','outcome':{'kind':'success'}}}]
                    if method=='update.apply' and not params.get('options',{}).get('dry_run'):
                        self.lost=True;raise OSError('requester connection closed')
                    return super().call(method,params,progress)
            engine=Lost();controller=self.make(tmp,engine);controller.upload(io.BytesIO(test_packages.Packages().outer()));self.finish(controller)
            controller.start('install');result=self.finish(controller)
            self.assertEqual(result['job']['phase'],'completed');self.assertEqual(result['installed'],'2026.10.27')

    def test_fixed_release_detail_retry_when_list_omits_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base={'id':42,'tag_name':'microduck-software-v2026.10.27','draft':False,'prerelease':False,'assets':[]}
            detail={**base,'assets':[{'name':'2026.10.27.software-update.tar','size':100,
                'browser_download_url':'https://github.com/fanhao375/microduck-replica/releases/download/microduck-software-v2026.10.27/2026.10.27.software-update.tar'}]}
            calls=[]
            def download(url,output,maximum):
                calls.append(url);output.write(json.dumps(detail if url.endswith('/42') else [base]).encode())
            controller=self.make(tmp)
            with mock.patch('control.fetch',side_effect=download):
                result=choose_release(controller.remote_catalog())
            self.assertEqual(result['version'],'2026.10.27');self.assertEqual(len(calls),2)

if __name__=='__main__': unittest.main()
