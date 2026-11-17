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
from control import Controller, UpdateError, check_robot, choose_release,atomic_json
import test_packages

class Engine:
    def __init__(self): self.calls=[]; self.wait=None
    def call(self,method,params=None,progress=None):
        self.calls.append((method,params))
        if method=='update.status': return [{'component':'software','installed':'2026.10.26','phase':'idle'}]
        if method=='update.listInstalled': return [{'version':'2026.10.26','active':True}]
        if self.wait: self.wait.wait(2)
        return {'outcome':'dry_run_passed' if params.get('options',{}).get('dry_run') else 'applied','candidate':'2026.10.27','to':'2026.10.27'}

def safe_bus():
    return {'mode':'motion','phase':'control','homed':True,'policy_enabled':False,'fresh_sample':True,
        'all_joints_fresh':True,'native':{'joints':[{'id':n,'feedback_fresh':True,'sample_age_ms':3,
            'position_rad':0,'velocity_rad_s':0,'volts':7.8} for n in range(15)]}}

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

    def test_unknown_or_moving_refused_but_low_power_is_not(self):
        check_robot(safe_bus(),{'battery':{'percent':70,'volts':7.8}},safe_bus())
        for key,value in (('mode','commissioning'),('phase','moving'),('homed',None),('policy_enabled',True),('all_joints_fresh',False)):
            bus=safe_bus(); bus[key]=value
            with self.assertRaises(UpdateError): check_robot(bus,{'battery':{'percent':70,'volts':7.8}},safe_bus())
        for health in ({},{'battery':{'percent':14,'volts':6.83}}):     # 11-18 duck-c84e 边充电边装时读到的
            check_robot(safe_bus(),health,safe_bus())
        moving=safe_bus(); moving['native']['joints'][0]['velocity_rad_s']=.4
        with self.assertRaises(UpdateError): check_robot(moving,{'battery':{'percent':70,'volts':7.8}},safe_bus())

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
