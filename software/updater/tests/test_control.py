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
                        'component':'software','from':'2026.10.26','outcome':{'kind':'rolled_back','reason':'health failed'},'to':'2026.10.27'}}]
                    return super().call(method,params,progress)
            started=int(time.time())-1
            atomic_json(Path(tmp)/'job.json',{'id':'persistent','operation':'install','phase':'health_gate','started_at':started,
                        'version':'2026.10.27','dispatch':{'at':started,'from':'2026.10.26','to':'2026.10.27','before':None}})
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
                        'phase':'idle','last_attempt':{'at':int(time.time()),'component':'software',
                        'from':'2026.10.26','to':'2026.10.27','outcome':{'kind':'success'}}}]
                    if self.lost and method=='update.listInstalled':return [{'version':'2026.10.27','active':True}]
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


    @staticmethod
    def stable_release(version='2026.10.27', size=10240):
        tag=f'microduck-software-v{version}'
        name=f'{version}.software-update.tar'
        return {'tag_name':tag, 'draft':False, 'prerelease':False, 'body':'release notes',
                'assets':[{'name':name, 'size':size, 'browser_download_url':
                           f'https://github.com/fanhao375/microduck-replica/releases/download/{tag}/{name}'}]}

    def online_controller(self, tmp, *, engine=None, preflight=lambda:None, release=None, data=None, catalog=None):
        data=test_packages.Packages().outer() if data is None else data
        release=release or self.stable_release(size=len(data))
        return Controller(tmp, engine or Engine(), preflight, catalog=catalog or (lambda:[release]),
                          fetcher=lambda url, stream, maximum: stream.write(data))

    def test_one_click_pins_checked_version_and_runs_two_separate_preflights(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine()
            preflights=[]
            def preflight():
                self.assertTrue(controller.marker.exists())
                preflights.append(len([p for m,p in engine.calls if m=='update.apply']))
            controller=self.online_controller(tmp,engine=engine,preflight=preflight)
            with self.assertRaises(UpdateError): controller.start('online:2026.10.27')
            controller.start('check')
            checked=self.finish(controller)
            self.assertTrue(checked.get('online_update'))
            self.assertEqual(checked['channel'],'stable')
            self.assertAlmostEqual(checked['catalog_checked_at'],time.time(),delta=3)
            for op in ('online:2026.10.28','online:2026.10.27-rc.1','online:../../cache','online','check_preview'):
                with self.subTest(op=op), self.assertRaises(UpdateError): controller.start(op)
            release_download=threading.Event()
            download_started=threading.Event()
            fetch=controller.fetcher
            def download(*args):
                download_started.set()
                if not release_download.wait(2): raise TimeoutError()
                return fetch(*args)
            controller.fetcher=download
            job=controller.start('online:2026.10.27')['job']
            try:
                self.assertTrue(download_started.wait(1))
                self.assertEqual(controller.start('check_auto')['job']['id'],job['id'])
                for op in ('check','rollback','online:2026.10.27'):
                    with self.assertRaises(UpdateError) as busy: controller.start(op)
                    self.assertEqual(busy.exception.code,'busy')
            finally: release_download.set()
            result=self.finish(controller)
            self.assertEqual(result['job']['phase'],'completed')
            self.assertEqual(result['job']['id'],job['id'])
            self.assertEqual(result['job']['operation'],'online')
            self.assertEqual(result['job']['version'], '2026.10.27')
            self.assertEqual(result['job']['result']['to'],'2026.10.27')
            self.assertFalse(result['ready'])
            self.assertEqual(preflights,[0,1])
            calls=[p for m,p in engine.calls if m=='update.apply']
            self.assertEqual([p['target'] for p in calls],[{'exact':'2026.10.27'}]*2)
            self.assertEqual([p['options'].get('dry_run',False) for p in calls],[True,False])

    def test_online_rejects_mismatched_manifest_or_catalog_size_before_verification(self):
        data=test_packages.Packages().outer()
        for version,size in [('2026.10.28',len(data)),('2026.10.27',len(data)-1)]:
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                engine=Engine()
                controller=self.online_controller(tmp, engine=engine,release=self.stable_release(version,size))
                controller.start('check'); self.finish(controller)
                controller.start('online:'+version)
                after=self.finish(controller)
                self.assertEqual(after['job']['phase'],'failed')
                self.assertIsNone(after['staged'])
                self.assertFalse(any(m=='update.apply' for m,_ in engine.calls))

    def test_requires_exact_engine_signature_dry_run_and_idle_model_at_apply(self):
        for mode in ['unsigned','wrong_candidate','already_current','model_retake']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                class Signature(Engine):
                    def call(self,method,params=None,progress=None):
                        result=super().call(method,params,progress)
                        if method=='update.apply' and params['options'].get('dry_run'):
                            if mode=='unsigned': raise UpdateError('engine','signature failed')
                            if mode=='wrong_candidate': result['candidate']='2026.10.25'
                            if mode=='already_current': result['outcome']='already_current'
                        return result
                engine=Signature()
                def preflight():
                    if mode=='model_retake' and any(m=='update.apply' for m,_ in engine.calls):
                        raise UpdateError('preflight','模型正在接管')
                controller=self.online_controller(tmp,engine=engine,preflight=preflight)
                controller.start('check'); self.finish(controller)
                controller.start('online:2026.10.27')
                result=self.finish(controller)
                self.assertEqual(result['job']['phase'],'waiting_validation' if mode=='model_retake' else 'failed')
                self.assertFalse(result['ready'])
                self.assertEqual([p['options'].get('dry_run') for m,p in engine.calls if m=='update.apply'],[True])

    def test_engine_identity_and_semver_checked_again_before_apply(self):
        for installed,active,source in [
                ('2026.10.27-rc.10','2026.10.27-rc.10','available'),
                ('2026.10.27+build','2026.10.27+build','up_to_date'),
                ('2026.10.28-rc.1','2026.10.28-rc.1','up_to_date'),
                ('?', '?','installed_unknown'),(None,None,'installed_unknown'),
                ('2026.10.26','2026.10.25','installed_unknown')]:
            with self.subTest(installed=installed), tempfile.TemporaryDirectory() as tmp:
                class Moving(Engine):
                    current=installed
                    active_version=active
                    def call(self,method,params=None,progress=None):
                        if method=='update.status':return [{'component':'software','installed':self.current,'phase':'idle'}]
                        if method=='update.listInstalled':return [{'version':self.active_version,'active':True}]
                        result=super().call(method,params,progress)
                        if method=='update.apply' and params['options'].get('dry_run'):
                            self.current=self.active_version='2026.10.28'
                        return result
                engine=Moving()
                controller=self.online_controller(tmp,engine=engine)
                controller.start('check'); checked=self.finish(controller)
                self.assertEqual(checked['source_state'],source)
                if source=='available':
                    controller.start('online:2026.10.27')
                    self.assertEqual(self.finish(controller)['job']['phase'],'failed')
                    self.assertEqual([p['options'].get('dry_run') for m,p in engine.calls if m=='update.apply'],[True])
                else:
                    with self.assertRaises(UpdateError): controller.start('online:2026.10.27')

    def test_auto_check_is_shared_throttled_and_preserves_operator_result_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks=[]
            def catalog():
                checks.append(1); return [self.stable_release()]
            controller=self.online_controller(tmp,catalog=catalog)
            controller.start('check_auto'); first=self.finish(controller)
            self.assertEqual(first['source_state'],'available')
            for _ in range(2):
                controller.start('check_auto'); self.finish(controller)
            self.assertEqual(len(checks),1)
            controller.start('online:2026.10.27'); completed=self.finish(controller)['job']
            restarted=self.online_controller(tmp,catalog=catalog)
            restarted.start('check_auto'); self.finish(restarted)
            self.assertEqual(len(checks),1)
            with mock.patch('control.time.time',return_value=time.time()+6*3600+1):
                restarted.start('check_auto'); view=self.finish(restarted)
                self.assertEqual(view['job'],completed)
                self.assertEqual(len(checks),2)

    def test_stale_check_and_failed_recheck_revoke_online_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller=self.online_controller(tmp)
            controller.start('check'); self.finish(controller)
            with mock.patch('control.time.time',return_value=time.time()+6*3600+1):
                with self.assertRaises(UpdateError):controller.start('online:2026.10.27')
            def unavailable(): raise UpdateError('network','offline')
            controller.catalog=unavailable
            controller.start('check');stale=self.finish(controller)
            self.assertEqual(stale['source_state'],'network_error')
            self.assertIsNone(stale['candidate'])
            with self.assertRaises(UpdateError):controller.start('online:2026.10.27')

    def test_available_requires_identity_confirmed_at_last_catalog_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Intermittent(Engine):
                offline=True
                def call(self,method,params=None,progress=None):
                    if self.offline and method=='update.status': raise OSError('engine offline')
                    return super().call(method,params,progress)
            engine=Intermittent()
            controller=self.online_controller(tmp,engine=engine)
            controller.start('check')
            self.assertEqual(self.finish(controller)['source_state'],'installed_unknown')
            engine.offline=False
            controller.refresh()
            with self.assertRaises(UpdateError):controller.start('online:2026.10.27')
            controller.start('check')
            self.assertEqual(self.finish(controller)['source_state'],'available')

    def test_concurrent_status_probes_cannot_publish_an_older_installed_snapshot_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_started=threading.Event()
            release_old=threading.Event()
            new_started=threading.Event()
            class Racing(Engine):
                def call(self,method,params=None,progress=None):
                    older=threading.current_thread().name=='old-probe'
                    ver='2026.10.26' if older else '2026.10.28'
                    if method=='update.status':
                        if not older:new_started.set()
                        return [{'component':'software','installed':ver,'phase':'idle'}]
                    if method=='update.listInstalled':
                        if older:
                            old_started.set()
                            release_old.wait(2)
                        return [{'active':True,'version':ver}]
                    return super().call(method,params,progress)
            controller=self.online_controller(tmp,engine=Racing())
            old=threading.Thread(target=controller.refresh,name='old-probe')
            new=threading.Thread(target=controller.refresh,name='new-probe')
            old.start()
            try:
                self.assertTrue(old_started.wait(1))
                new.start()
                self.assertFalse(new_started.wait(.05))
            finally:
                release_old.set()
                old.join(2)
                if new.ident is not None: new.join(2)
            self.assertEqual(controller.view()['installed'],'2026.10.28')

    def test_restart_never_promotes_validation_or_stale_engine_attempt_to_applied(self):
        for case in ('no_dispatch','same_record','wrong_target','not_installed','older_run','foreign_component','wrong_from'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                now=int(time.time())
                version='2026.10.27'
                attempt={'at':now,'component':'software','from':'2026.10.26','to':version,'outcome':{'kind':'success'},'run':9}
                class Finished(Engine):
                    def call(self,method,params=None,progress=None):
                        if method=='update.status':
                            return [{'component':'software','phase':'idle',
                                     'installed':'2026.10.26' if case=='not_installed' else version,
                                     'last_attempt':{**attempt,'from':'2026.10.25' if case=='wrong_from' else '2026.10.26',
                                                     'component':'native' if case=='foreign_component' else 'software',
                                                     'to':'2026.10.29' if case=='wrong_target' else version}}]
                        if method=='update.listInstalled':
                            return [{'active':True,'version':'2026.10.26' if case=='not_installed' else version}]
                        return super().call(method,params,progress)
                job={'id':'persisted','operation':'online','version':version,'phase':'verifying','started_at':now}
                if case!='no_dispatch':
                    job.update(phase='applying', dispatch={'at':now,'from':'2026.10.26','to':version,
                               'before':{'run':10,'at':now} if case=='older_run' else attempt if case=='same_record' else None})
                atomic_json(Path(tmp)/'job.json',job)
                engine=Finished()
                result=self.finish(self.make(tmp,engine))
                self.assertEqual(result['job']['phase'],'interrupted')
                self.assertFalse(any(m=='update.apply' for m,_ in engine.calls))

    def test_lost_rollback_reply_requires_new_journal_entry_for_previous_install(self):
        class LostRollback(Engine):
            current='2026.10.27'
            def call(self,method,params=None,progress=None):
                if method=='update.status':
                    entry={'component':'software','phase':'idle','installed':self.current,'last_attempt':None}
                    if self.current=='2026.10.26':
                        entry['last_attempt']={'component':'software','at':int(time.time()),'run':7,'from':'2026.10.27',
                                               'to':'2026.10.26','outcome':{'kind':'success'}}
                    return [entry]
                if method=='update.listInstalled':
                    return [{'version':v,'active':v==self.current} for v in ['2026.10.27','2026.10.26']]
                if method=='update.rollback':
                    self.current='2026.10.26'
                    raise OSError('reply lost')
                return super().call(method,params,progress)
        with tempfile.TemporaryDirectory() as tmp:
            controller=self.make(tmp,LostRollback())
            controller.start('rollback')
            result=self.finish(controller)
            self.assertEqual(result['job']['phase'],'completed')
            self.assertEqual(result['job']['result']['to'],'2026.10.26')

if __name__=='__main__': unittest.main()
