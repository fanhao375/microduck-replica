"""Native update integration: fixed authority, persistent truth and readiness."""
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_ai_http as http
from tests.test_http import WsClient
from studio import helper
from studio.updates import Updates, UpdateError

OLD = 'e' * 64
NEW = 'f' * 64


class NativeFinish(unittest.TestCase):
    def test_native_status_preserves_actual_unsupported_reason_when_finish_reason_absent(self):
        from tests.test_helper_bus import H,FakeCtx,World
        with tempfile.TemporaryDirectory() as tmp:
            ctx=FakeCtx(World(tmp),b'{}')
            H.Op(ctx,'robotd-update').finish(0,'native_status',detail={'reason':'未知实际主程序，拒绝自动安装'})
            self.assertEqual(ctx.stdout.lines()[-1]['detail']['reason'],'未知实际主程序，拒绝自动安装')


class NativeBridge(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = {'active':False, 'phase':'idle', 'available':True,
                      'running_verified':True, 'installed_sha256':OLD, 'target_sha256':NEW}
        self.calls = []
        self.hold = threading.Event()
        self.hold.set()
        def call(by, action, **kwargs):
            self.calls.append((by, action, kwargs))
            if action != 'status': self.hold.wait(3)
            return True, 'native_status', dict(self.state)
        self.updates = Updates(marker=Path(self.tmp.name)/'installing', native_call=call,
                               native_state=lambda:dict(self.state))

    async def asyncTearDown(self):
        self.hold.set()
        if self.updates.native_task: await self.updates.native_task
        self.tmp.cleanup()

    async def test_local_success_without_verified_running_bytes_is_never_current(self):
        result = await self.updates.native_request('install', confirmed=True)
        await self.updates.native_task
        result = await self.updates.native_status()
        self.assertFalse(result['current'])
        self.assertTrue(result['supported'])
        self.assertIn(('Studio', 'install', {'confirmed':True}),self.calls)
        self.state.update(installed_sha256=NEW, running_verified=False)
        self.assertFalse((await self.updates.native_status())['current'])
        self.state['running_verified'] = True
        self.assertTrue((await self.updates.native_status())['current'])
        with self.assertRaises(UpdateError):await self.updates.request('rollback')

    async def test_private_preparing_task_is_not_a_motion_or_readiness_guard(self):
        self.hold.clear()
        await self.updates.native_request('install', confirmed=True)
        self.assertFalse(self.updates.installing())
        with self.assertRaises(UpdateError):
            await self.updates.native_request('install', confirmed=True)
        self.state.update(active=True, phase='replacing')
        self.assertTrue(self.updates.installing())

    async def test_mutations_require_real_boolean_and_software_idle(self):
        for value in (None, False, 1, 'true', [], {}):
            with self.assertRaises(UpdateError):
                await self.updates.native_request('install', confirmed=value)
        self.updates.marker.touch()
        with self.assertRaises(UpdateError):
            await self.updates.native_request('recover', confirmed=True)
        self.assertEqual(self.calls, [])

    async def test_software_mutations_refuse_native_guard_but_status_and_download_work(self):
        self.state['active'] = True
        for op in ('install', 'rollback'):
            with self.assertRaises(UpdateError) as error: await self.updates.request(op)
            self.assertEqual(error.exception.code, 'busy')
        self.assertFalse((await self.updates.request())['available'])

    async def test_timeout_keeps_persisted_actual_state_authoritative(self):
        def timeout(*args, **kwargs): return False, 'TimeoutExpired', None
        self.updates.native_call = timeout
        self.state.update(active=True, phase='replacing')
        await self.updates.native_request('recover', confirmed=True)
        await self.updates.native_task
        status = await self.updates.native_status()
        self.assertTrue(status['active'])
        self.assertEqual(status['phase'], 'replacing')
        self.assertNotEqual(status.get('phase'), 'failed')

    async def test_concurrent_submissions_cannot_both_pass_async_software_check(self):
        original=self.updates.request
        async def slow(operation='status'):
            await asyncio.sleep(.02)
            return await original(operation)
        self.updates.request=slow
        self.hold.clear()
        results=await asyncio.gather(*(self.updates.native_request('install',confirmed=True) for _ in range(2)),return_exceptions=True)
        self.assertEqual(sum(isinstance(row,UpdateError) for row in results),1,results)

    async def test_bundle_current_requires_complete_component_vector_and_explicit_restore(self):
        self.state.update(installed_sha256=NEW,bundle_schema='microduck-controller-payload/1',bundle_current=False,
            restorable=False,components={'robotd':{'installed_sha256':NEW},'padd':{'installed_sha256':OLD}})
        result=await self.updates.native_status()
        self.assertFalse(result['current'],'robotd .47 alone is not a current controller bundle')
        self.assertIn('padd',result['components'])
        self.state.update(bundle_current=True,restorable=True)
        self.assertTrue((await self.updates.native_status())['current'])
        await self.updates.native_request('restore',confirmed=True)
        await self.updates.native_task
        self.assertIn(('Studio','restore',{'confirmed':True}),self.calls)


class NativeHttp(unittest.IsolatedAsyncioTestCase):
    call = http.AiHttp.call
    asyncTearDown = http.AiHttp.asyncTearDown

    async def asyncSetUp(self):
        self.native = {'active':False, 'phase':'idle', 'available':True,
                       'running_verified':True, 'installed_sha256':OLD, 'target_sha256':NEW}
        self.native_calls = []
        def call(by, action, **kwargs):
            self.native_calls.append((by,action,kwargs))
            return True, 'native_status', dict(self.native)
        self.updates = Updates(native_call=call, native_state=lambda:dict(self.native))
        original_build=http.build_app
        def build(*args,**kwargs):
            from studio.servo import ServoSession
            self.servo=ServoSession(kwargs['log'],sim=True)
            return original_build(*args,servo=self.servo,**kwargs)
        with mock.patch('studio.app.Updates', return_value=self.updates),mock.patch.object(http,'build_app',build):
            await http.AiHttp.asyncSetUp(self)

    async def test_fixed_native_http_schema_and_authentication(self):
        for headers, expected in (({},401), ({'Origin':'http://evil.example'},401),
                                  ({**self.headers,'X-CSRF':'wrong'},403),
                                  ({**self.headers,'Origin':'http://evil.example'},403)):
            status,_,_=await self.call('POST','/api/updates',{'op':'native_install','confirmed':True},headers)
            self.assertEqual(status,expected)
        for body in ({'op':'native_install'}, {'op':'native_install','confirmed':1},
                     {'op':'native_install','confirmed':True,'by':'Other'},
                     {'op':'native_recover','confirmed':True,'force':True},
                     {'op':'native_status','path':'/tmp/x'},
                     {'op':'native_install','confirmed':True,'url':'https://evil.example'},
                     {'op':'native_install','confirmed':True,'command':'id'}):
            status,_,_=await self.call('POST','/api/updates',body,self.headers)
            self.assertEqual(status,400,body)
        self.assertFalse(any(action!='status' for _,action,_ in self.native_calls))
        status,headers,result=await self.call('POST','/api/updates',{'op':'native_install','confirmed':True},self.headers)
        self.assertEqual(status,200,result)
        self.assertEqual(headers['cache-control'],'no-store')
        await self.updates.native_task
        self.assertIn(('Studio','install',{'confirmed':True}),self.native_calls)

    async def test_persisted_guard_blocks_writes_and_keeps_real_emergency_path(self):
        self.native.update(active=True,phase='recovery_required')
        client=WsClient(self.port,origin=self.origin,cookie=self.headers['Cookie'].split('=',1)[1])
        await client.open()
        try:
            for index,(name,args) in enumerate((('servo.open',{}),('imu.op',{'op':'set_profile'}),
                    ('onboard.op',{'op':'done'}),('pad.op',{'op':'audio','music':True}),
                    ('pad.op',{'op':'motion','action':'record'}),('tune.op',{'op':'write'}),('calib.op',{}),
                    ('relax',{}))):
                cid=str(index)
                await client.send({'t':'cmd','id':cid,'name':name,'args':args})
                await client.pump(until=lambda:any(m.get('id')==cid and m.get('t')=='result' for m in client.msgs))
                result=next(m for m in client.msgs if m.get('id')==cid and m.get('t')=='result')
                self.assertEqual(result['ok'],name=='relax',result)
        finally: client.close()

    async def test_bundle_restore_requires_auth_csrf_and_boolean_confirmation(self):
        for body in ({'op':'native_restore'},{'op':'native_restore','confirmed':1}):
            status,_,_=await self.call('POST','/api/updates',body,self.headers);self.assertEqual(status,400)
        status,_,_=await self.call('POST','/api/updates',{'op':'native_restore','confirmed':True},{})
        self.assertEqual(status,401)
        status,_,_=await self.call('POST','/api/updates',{'op':'native_restore','confirmed':True},self.headers)
        self.assertEqual(status,200)
        await self.updates.native_task
        self.assertIn(('Studio','restore',{'confirmed':True}),self.native_calls)

    async def test_real_prejournal_battery_refusal_reaches_helper_bridge_and_http(self):
        from tests.test_native_update import NativeTest,H,STATE
        fixture=NativeTest();fixture.setUp();self.addCleanup(fixture.doCleanups)
        original=fixture.ctx.rpc
        fixture.ctx.rpc=lambda method,*args,**kwargs: ({'battery':{'volts':6.5,'percent':10}}
            if method=='robot.health' else original(method,*args,**kwargs))
        def root(by,action,**kwargs):
            if action=='status':return True,'native_status',dict(self.native)
            try:fixture.update(action)
            except H.Stop as refusal:
                fixture.op.finish(refusal.code,refusal.result,refusal.reason,refusal.detail)
            raw=fixture.ctx.stdout.buf.getvalue().decode('utf-8')
            with mock.patch('studio.helper.subprocess.run',return_value=SimpleNamespace(stdout=raw,stderr='',returncode=4)):
                return helper.robotd_update(by,action,**kwargs)
        self.updates.native_call=root
        status,_,result=await self.call('POST','/api/updates',{'op':'native_install','confirmed':True},self.headers)
        self.assertEqual(status,200,result)
        await self.updates.native_task
        status,_,result=await self.call('GET','/api/updates',headers=self.headers)
        self.assertEqual(status,200,result)
        self.assertIn('电量不足或无法确认供电',result['native']['request_error'])
        self.assertFalse(fixture.w.path(STATE+'/journal.json').exists())

    async def test_malformed_persisted_journal_overrides_healthy_status_after_restart(self):
        journal=Path(self.tmp.name)/helper.NATIVE_UPDATE_JOURNAL.lstrip('/')
        journal.parent.mkdir(parents=True);journal.write_text('{broken',encoding='utf-8')
        self.updates.native_state=lambda:helper.native_update_state(root=self.tmp.name)
        status,_,result=await self.call('GET','/api/updates',headers=self.headers)
        self.assertEqual(status,200,result)
        self.assertTrue(result['native']['active'])
        self.assertEqual(result['native']['phase'],'recovery_required')
        self.assertFalse(result['native']['current'])
        for op in ('install','rollback'):
            status,_,result=await self.call('POST','/api/updates',{'op':op},self.headers)
            self.assertEqual(status,409,result)

    async def test_unpublished_recovery_uses_real_app_readiness_without_self_deadlock(self):
        from tests.test_controller_bundle import BundleTransaction,C
        from tests.test_native_update import N,H
        fixture=BundleTransaction();fixture.setUp();self.addCleanup(fixture.doCleanups)
        class PowerLoss(BaseException):pass
        write=C.BundleTransaction.write
        def die(transaction,path,*args,**kwargs):
            result=write(transaction,path,*args,**kwargs)
            if path==C.PREPARED:raise PowerLoss()
            return result
        with mock.patch.object(C.BundleTransaction,'write',die),self.assertRaises(PowerLoss):fixture.update()
        self.updates.native_state=lambda:helper.native_update_state(root=fixture.tmp)
        self.updates.marker=Path(self.tmp.name)/'software-installing'
        callback=self.updates.readiness_snapshot
        self.assertFalse(callback()['ready'],'ordinary software readiness must keep the guard')
        original=fixture.socket
        def root_socket(path,payload):
            if path.endswith('/microduck-studio/update.sock'):
                return callback(native_transaction=payload['transaction_id']) if payload.get('op')=='native-recovery-readiness' else callback()
            return original(path,payload)
        with mock.patch.object(N,'json_socket',side_effect=root_socket):
            try:result=fixture.update('recover')
            except H.Stop as error:self.fail('own prepared guard blocked recovery: '+error.reason)
        self.assertEqual(result,0)
        self.assertTrue(callback()['ready'])
        intent={'schema':'microduck-controller-update/1','bundle_schema':C.PAYLOAD_SCHEMA,'active':True,
                'phase':'prepared','transaction_id':'a'*32,'restore_pending':False,'cleanup_pending':False}
        fixture.w.path(C.PREPARED).write_text(json.dumps(intent),encoding='utf-8')
        self.assertFalse(callback()['ready'])
        self.assertFalse(callback(native_transaction='b'*32)['ready'])
        self.assertTrue(callback(native_transaction='a'*32)['ready'])
        self.servo.state='opening'
        self.assertFalse(callback(native_transaction='a'*32)['ready'],'real servo activity remains a refusal')
        self.servo.state='closed'
        self.updates.marker.touch()
        self.assertFalse(callback(native_transaction='a'*32)['ready'],'software marker is never ignored')

    async def test_software_updater_passes_readiness_with_its_own_marker_present(self):
        """升级器先写「正在安装」标记再来问空不空闲（updater/control.py 的 run → validate → preflight）。
        标记是它自己的，算进去就永远装不上；主程序升级那条路是自己查这个标记的，不靠这里。"""
        self.updates.marker=Path(self.tmp.name)/'software-installing'
        callback=self.updates.readiness_snapshot
        self.assertTrue(callback()['ready'])
        self.updates.marker.write_text('job-1',encoding='utf-8')
        self.assertTrue(callback()['ready'],'the software updater must pass its own preflight')
        self.servo.state='opening'
        self.assertFalse(callback()['ready'],'real servo activity remains a refusal')
        self.servo.state='closed'
        self.native.update(active=True,phase='guarded')
        self.assertFalse(callback()['ready'],'a native update in progress still refuses')
        self.native.update(active=False,phase='idle')
        self.assertFalse(callback(native_transaction='a'*32)['ready'],'native recovery still sees the software marker')

    @unittest.skipUnless(os.name=='posix','real Linux root/app readiness socket')
    async def test_actual_readiness_socket_answers_the_software_updater_during_its_install(self):
        snapshot=self.updates.readiness_snapshot
        await self.updates.stop_readiness()
        self.updates.readiness=Path(self.tmp.name)/'software-ready.sock'
        self.updates.marker=Path(self.tmp.name)/'software-installing'
        await self.updates.start_readiness(snapshot)
        def ask(request):
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(2);sock.connect(str(self.updates.readiness))
                sock.sendall(json.dumps(request).encode()+b'\n')
                return json.loads(sock.recv(8192))
        self.updates.marker.write_text('job-1',encoding='utf-8')
        self.assertEqual(await asyncio.to_thread(ask,{'op':'readiness'}),{'ready':True})
        recovery=await asyncio.to_thread(ask,{'op':'native-recovery-readiness','transaction_id':'a'*32})
        self.assertFalse(recovery['ready'])

    @unittest.skipUnless(os.name=='posix','real Linux root/app readiness socket')
    async def test_actual_readiness_socket_before_journal_and_after_guard(self):
        # Lifespan already started; restart the actual server with its original callback.
        snapshot=self.updates.readiness_snapshot
        await self.updates.stop_readiness()
        self.updates.readiness=Path(self.tmp.name)/'native-ready.sock'
        await self.updates.start_readiness(snapshot)
        journal=Path(self.tmp.name)/helper.NATIVE_UPDATE_JOURNAL.lstrip('/')
        journal.parent.mkdir(parents=True)
        self.updates.native_state=lambda:helper.native_update_state(root=self.tmp.name)
        observations=[]
        def readiness():
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(2);sock.connect(str(self.updates.readiness))
                sock.sendall(b'{"op":"readiness"}\n')
                return json.loads(sock.recv(8192))
        def root(by,action,**kwargs):
            if action=='install':
                observations.append(readiness())
                self.native.update(active=True,phase='guarded')
                journal.write_text(json.dumps({'schema':'microduck-native-update/1','transaction_id':'a'*32,**self.native}),encoding='utf-8')
                observations.append(readiness())
            return True,'native_status',dict(self.native)
        self.updates.native_call=root
        status,_,result=await self.call('POST','/api/updates',{'op':'native_install','confirmed':True},self.headers)
        self.assertEqual(status,200,result)
        await self.updates.native_task
        self.assertEqual([row['ready'] for row in observations],[True,False])


if __name__=='__main__':unittest.main()
