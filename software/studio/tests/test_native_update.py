"""Native updater boundary tests; temporary files and simulated systemd, no hardware."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.test_helper_imu_redo import RedoWorld
from tests.test_helper_calib import CalibCtx, H

MODULE = Path(__file__).resolve().parents[1] / 'deploy/native_update.py'
N = None
if MODULE.exists():
    spec = importlib.util.spec_from_file_location('native_update', MODULE)
    N = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(N)


def elf(tail):
    return b'\x7fELF\x02\x01\x01' + bytes(9) + b'\x02\x00\xb7\x00' + bytes(44) + tail


OLD, PREVIOUS, STARTUP, PUBLISHED, NEW = (elf(b'old'), elf(b'previous-reviewed'),
                              elf(b'startup-status-reviewed'), elf(b'published47'), elf(b'new'))
SHA = lambda data: hashlib.sha256(data).hexdigest()
APPROVED_SHA256 = 'cbda6305061ce6506ab116532d16c9903c6ebb1af866f15c61ac712ce537bcb1'
PREDECESSOR_SHA256 = ('edf182edb7bf11aeeeedfdab98170ab2ec48a9ec18417f9b68c744b0a60c6d16',
                      'febf44e2e5d4e573f4513f25a1ae6480b976c3988dd3d5e7cdfcf84d2a2d5063',
                      '3dbbf1723c0315afc15fd9f28787c0cfc194622a67802adcd1f5aa397ea47422',
                      '725b74cd93660a312970a44879abe5b75e8becf108499a1b8798961e48ca5f00',
                      'e5ff7fb429fd0dfb762eb4da1bcc5a26b48b6d2dfdae4bb4904213e37e022ed0')
TARGET = '/opt/robot/feetech-ft5-r5/robotd'
ARGS = [TARGET, '--socket', '/run/robotd.sock', '--params', H.IMU_CONFIG, '--port', '/dev/ttyS2']
STATE = '/var/lib/microduck-helper/native-update'
GUARD = '/etc/systemd/system/robotd.service.d/zzzzzzz-microduck-native-update.conf'
FILES = (H.IMU_CONFIG, H.IMU_CONFIG_SAVED, H.CALIB, H.CALIB_SAVED)


class NativeReleaseTest(unittest.TestCase):
    def artifact(self):
        relative = 'chengshenzhilu/Robot/Microduck/artifacts/dual-images-20261112/zero3w-rc3/native/robotd-control51-aarch64'
        for root in ('F:/', '/mnt/f/'):
            path = Path(root + relative)
            if path.exists(): return path.read_bytes()
        self.skipTest('reviewed local release input not present')

    def test_reviewed_identity_and_supported_predecessors_are_exact(self):
        self.assertEqual(N.REVIEWED_SHA256, APPROVED_SHA256)
        self.assertEqual(N.REVIEWED_SIZE, 7160816)
        self.assertEqual(N.SOURCE, '0a492d14457d4bc8f4940d941959c05ca1101140')
        self.assertEqual(N.BUILD, 'replica-bus-health-control.51')
        self.assertEqual(N.IPC_API, 44)
        self.assertEqual(N.SUPPORTED_SHA256, PREDECESSOR_SHA256)
        self.assertEqual((N.LEGACY_OLD_SHA256, N.LEGACY_TARGET_SHA256), PREDECESSOR_SHA256[:2])
        self.assertNotIn(N.REVIEWED_SHA256, N.SUPPORTED_SHA256)

    def test_known_predecessor_contracts_and_journal_pairs_are_exact(self):
        baseline, previous = PREDECESSOR_SHA256[:2]
        startup = '3dbbf1723c0315afc15fd9f28787c0cfc194622a67802adcd1f5aa397ea47422'
        self.assertEqual(N.LEGACY_STARTUP_SHA256, startup)
        published, verify = PREDECESSOR_SHA256[3:]
        self.assertEqual(N.LEGACY_PUBLISHED_SHA256,published)
        self.assertEqual(N.PREDECESSOR_IPC_API, {baseline: 40, previous: 40, startup: 41,published:42,verify:43})
        self.assertEqual(N.LEGACY_TRANSACTIONS, {(baseline, previous): '.45',
                                               (baseline, startup): '.46', (previous, startup): '.46',
                                               **{(old,published):'.47' for old in (baseline,previous,startup)},
                                               **{(old,verify):'.48' for old in (baseline,previous,startup,published)}})

    def test_actual_approved_artifact_matches_exact_manifest(self):
        data = self.artifact()
        self.assertTrue(N.is_aarch64(data))
        self.assertEqual(len(data), 7160816)
        self.assertEqual(SHA(data), APPROVED_SHA256)
        self.assertEqual(N.native_manifest('2026.10.30', data), {
            'schema': 'microduck-native-payload/1', 'architecture': 'aarch64',
            'software_version': '2026.10.30',
            'source_revision': '0a492d14457d4bc8f4940d941959c05ca1101140',
            'build': 'replica-bus-health-control.51', 'official_version': '0.15.1',
            'model_api': 2, 'ipc_api': 44, 'size': 7160816, 'sha256': APPROVED_SHA256,
            'supported_installed_sha256': list(PREDECESSOR_SHA256)})

    def test_actual_approved_payload_is_accepted(self):
        data = self.artifact()
        try:
            N.validate_payload(data, N.native_manifest('2026.10.30', data), '2026.10.30')
        except ValueError as error:
            self.fail(str(error))

    def test_actual_payload_refuses_altered_manifest_or_bytes(self):
        data = self.artifact()
        manifest = N.native_manifest('2026.10.30', data)
        for field, value in (('schema', 'foreign'), ('ipc_api', 40), ('ipc_api', 41), ('ipc_api',42), ('model_api', 3),
                             ('source_revision', 'a' * 40), ('build', 'foreign'),
                             ('supported_installed_sha256', []), ('unexpected', True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                N.validate_payload(data, {**manifest, field: value}, '2026.10.30')
        with self.assertRaises(ValueError):
            N.validate_payload(data[:-1] + bytes([data[-1] ^ 1]), manifest, '2026.10.30')

    def test_actual_predecessor46_cannot_be_reused_as_current_payload(self):
        relative = 'chengshenzhilu/Robot/Microduck/artifacts/imu-only-20261029/robotd-startup-status-arm64'
        for root in ('F:/', '/mnt/f/'):
            path = Path(root + relative)
            if path.exists():
                data = path.read_bytes()
                break
        else:
            self.skipTest('reviewed predecessor release input not present')
        self.assertEqual(SHA(data), PREDECESSOR_SHA256[2])
        with self.assertRaises(ValueError):
            N.validate_payload(data, N.native_manifest('2026.10.30', data), '2026.10.30')


class NativeWorld(RedoWorld):
    def __init__(self, root):
        super().__init__(root)
        self.pid = 200
        self.boot = 'test-boot'
        self.fail = None
        self.hook = None
        self.absent_units=set()
        self.hello_apis = {OLD: 40, PREVIOUS: 40, STARTUP: 41, PUBLISHED: 42, NEW: 44}
        self.hello_seen = []
        self.units['duck-echo'] = 'active'
        self.exec_override = ' '.join(ARGS)
        self.path(TARGET).parent.mkdir(parents=True)
        self.path(TARGET).write_bytes(OLD)
        os.chmod(self.path(TARGET), 0o755)
        self.path('/proc/sys/kernel/random').mkdir(parents=True)
        self.path(H.BOOT_ID).write_text(self.boot)
        self.publish_process()
        self.ctx_factory=NativeCtx

    def reboot(self):
        self.boot='new-boot-'+self.boot
        self.locks.clear()
        for path in (N.PROTECTED,N.RESTORE_PERMIT,H.LAST):
            if self.path(path).exists():self.path(path).unlink()
        self.pid+=1000
        self.effective_commissioning=self.path(GUARD).exists() or self.path(H.COMMISSIONING_DROPIN).exists()
        self.live_mode='commissioning' if self.effective_commissioning else 'motion'
        self.robotd='active';self.publish_process()
        for unit in N.CONTROLLERS:
            self.units[unit]='inactive' if self.path(N.ACTIVE).exists() or unit in self.absent_units else 'active'

    def _rpc(self, method, params=None):
        if method == 'robot.health': return {'battery':{'volts':7.7,'percent':60}}
        return super()._rpc(method, params)

    def publish_process(self):
        p = self.path('/proc/' + str(self.pid))
        p.mkdir(parents=True, exist_ok=True)
        if (p/'exe').exists(): (p/'exe').unlink()
        os.link(self.path(TARGET), p/'exe')
        (p/'stat').write_text(str(self.pid) + ' (robotd) S ' + ' '.join(['0'] * 18 + [str(self.pid)]))
        (p/'cmdline').write_bytes(b'\0'.join(a.encode() for a in ARGS + (['--commissioning'] if self.live_mode == 'commissioning' else [])) + b'\0')

    def show(self, unit, props):
        values = super().show(unit, props)
        if unit == 'robotd':
            values = values.replace('path=/opt/robot/robotd ;', 'path='+TARGET+' ;')
            values += f'\nMainPID={self.pid}\nInvocationID=inv-{self.pid}\nExecMainStartTimestampMonotonic={self.pid}\n'
            values += '\nDropInPaths=/etc/systemd/system/robotd.service.d/zzzz-r17-0151.conf ' + (GUARD if self.path(GUARD).exists() else '') + '\n'
        if unit == 'padd':
            values += '\nMainPID=20\nInvocationID=pad-generation\nExecMainStartTimestampMonotonic=20\n'
        if unit in N.CONTROLLERS:
            state=self.units.get(unit,'inactive')
            sub={'active':'running','inactive':'dead','failed':'failed','activating':'start-pre',
                 'deactivating':'stop-sigterm','reloading':'reload'}.get(state,'unknown')
            values+='\nLoadState='+('not-found' if unit in self.absent_units else 'loaded')+'\nSubState='+sub+'\n'
        return values

    def run(self, argv):
        if self.hook: self.hook(argv)
        if argv == ['systemctl', 'daemon-reload']:
            self.effective_commissioning = self.path(GUARD).exists() or self.path(H.COMMISSIONING_DROPIN).exists()
            return 0, '', ''
        if argv[:3] == ['systemctl', 'stop', 'robotd']:
            self.calls.append(argv)
            self.robotd = 'inactive'
            return 0, '', ''
        if argv[:3] == ['systemctl', 'start', 'robotd']:
            self.calls.append(argv)
            if self.fail == 'start': return 1, '', 'injected failure'
            self.robotd = 'active'
            self.live_mode = 'commissioning' if self.effective_commissioning else 'motion'
            self.pid += 1
            self.publish_process()
            return 0, '', ''
        if argv == [TARGET, 'deployment-info']:
            data=self.path(TARGET).read_bytes()
            build = ('replica-bus-health-control.51' if data==NEW else
                     '0791fcd-feetech-qinmo-control'+{OLD: '.44', PREVIOUS: '.45', STARTUP: '.46', PUBLISHED: '.47'}[data])
            return 0, json.dumps({'build': build, 'model_api': 2, 'official_version': '0.15.1'}), ''
        return super().run(argv)


class NativeCtx(CalibCtx):
    def boot_id(self):return self.w.boot

    def rpc(self, method, params=None, timeout=1.0):
        result = super().rpc(method, params, timeout)
        if method == 'hello':
            running = self.w.path('/proc/' + str(self.w.pid) + '/exe').read_bytes()
            result['api_version'] = self.w.hello_apis.get(running, -1)
            self.w.hello_seen.append((running, self.w.live_mode, result['api_version']))
        return result

    def lock(self,path):
        handle=super().lock(path)
        if handle is not None and not os.path.lexists(self.p(path)):
            with open(self.p(path),'xb'):pass
            os.chmod(self.p(path),0o600)
        return handle


class NativeTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(N, 'native update module must exist')
        self.tmp = tempfile.mkdtemp(prefix='native-update-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.w = NativeWorld(self.tmp)
        for i, path in enumerate(FILES):
            p = self.w.path(path); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes((f'# preserve exact file {i}\nfeetech_calibration = "{H.CALIB}"\n[bus]\nprotocol = "feetech"\n').encode())
        self.saved = {p: self.w.path(p).read_bytes() for p in FILES}
        self.release = '/opt/microduck/software/releases/2026.10.28'
        for name, data in [('studio/deploy/native/robotd', NEW), ('studio/deploy/native/manifest.json', b'{}')]:
            p = self.w.path(self.release + '/' + name); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
        self.ctx = NativeCtx(self.w, b'{}')
        self.op = H.Op(self.ctx, 'robotd-update')
        self.pins = mock.patch.multiple(N, REVIEWED_SHA256=SHA(NEW), REVIEWED_SIZE=len(NEW),
            BUILD='replica-bus-health-control.51', IPC_API=44,
            SUPPORTED_SHA256=(SHA(OLD), SHA(PREVIOUS), SHA(STARTUP), SHA(PUBLISHED)),
            LEGACY_OLD_SHA256=SHA(OLD), LEGACY_TARGET_SHA256=SHA(PREVIOUS), LEGACY_STARTUP_SHA256=SHA(STARTUP),
            PREDECESSOR_IPC_API={SHA(OLD): 40, SHA(PREVIOUS): 40, SHA(STARTUP): 41, SHA(PUBLISHED):42},
            LEGACY_TRANSACTIONS={(SHA(OLD), SHA(PREVIOUS)): '.45',
                                 (SHA(OLD), SHA(STARTUP)): '.46',
                                 (SHA(PREVIOUS), SHA(STARTUP)): '.46',
                                 **{(SHA(old),SHA(PUBLISHED)):'.47' for old in (OLD,PREVIOUS,STARTUP)}})
        self.pins.start(); self.addCleanup(self.pins.stop)
        published=mock.patch.object(N,'LEGACY_PUBLISHED_SHA256',SHA(PUBLISHED),create=True)
        published.start();self.addCleanup(published.stop)
        self.manifest = N.native_manifest('2026.10.28', NEW)
        self.w.path(self.release + '/studio/deploy/native/manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        for path in (N.MOTION_LOCK,H.SERVICE_LOCK,'/etc/systemd/system/robotd.service.d/zzzz-r17-0151.conf'):
            p=self.w.path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'')
        self.readiness = mock.patch.object(N, 'json_socket', side_effect=self.socket)
        self.readiness.start(); self.addCleanup(self.readiness.stop)
        self.disk = mock.patch.object(N.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(2**30,0,2**30))
        self.disk.start(); self.addCleanup(self.disk.stop)
        self.w.bus_mutate = self.fresh

    def fresh(self, bus):
        bus.update(fresh_sample=True, all_joints_fresh=True)
        self.w.motion_cycle=getattr(self.w,'motion_cycle',0)+1
        bus['cycle']=self.w.motion_cycle
        bus.setdefault('native', {})['joints'] = [{'id': i, 'feedback_fresh': True, 'sample_age_ms': 10, 'velocity_rad_s': 0., 'position_rad': 0.} for i in sorted(H.JOINT_IDS)]
        if bus['mode']=='commissioning':
            self.w.cycle=getattr(self.w,'cycle',0)+1
            bus.update(policy_motion_enabled=False,power_pending=False,updated_at_us=self.w.cycle*300000,
                       last_sample={'cycle':self.w.cycle,'at_us':self.w.cycle*300000,
                       'servos':[{'id':i,'position_raw':2048,'speed_raw':0,'moving':False} for i in H.JOINT_IDS]})

    def socket(self, path, payload):
        self.assertFalse(self.w.path(STATE+'/journal.json').exists(), 'readiness must precede own journal')
        return {'ready': True} if payload == {'op':'readiness'} else {'ok':True,'status':{'listening':False,'music':False}}

    def update(self, action='install'):
        return N.handle(H, self.op, {'by':'Studio','action':action, **({'confirmed':True} if action!='status' else {})}, self.release)

    def journal(self):
        return json.loads(self.w.path(STATE+'/journal.json').read_text(encoding='utf-8'))

    def test_normal_install_verifies_real_process_and_preserves_exact_user_files(self):
        self.assertEqual(self.update(), 0)
        self.assertEqual(self.w.path(TARGET).read_bytes(), NEW)
        self.assertFalse(self.journal()['active'])
        self.assertEqual(self.journal()['phase'], 'completed')
        self.assertTrue(self.journal()['running_verified'])
        self.assertFalse(self.w.path(GUARD).exists())
        self.assertEqual(self.w.live_mode, 'motion')
        self.assertTrue(all(self.w.path(p).read_bytes()==v for p,v in self.saved.items()))
        self.assertEqual(self.w.units['padd'], 'active')
        self.assertEqual(self.w.hello_seen, [(NEW, 'commissioning', 44), (NEW, 'motion', 44)])

    def test_previous_reviewed_binary_can_upgrade_to_current(self):
        self.w.path(TARGET).write_bytes(PREVIOUS)
        self.w.publish_process()
        self.assertEqual(self.update(), 0)
        self.assertEqual(self.journal()['old_sha256'], SHA(PREVIOUS))
        self.assertEqual(self.w.path(TARGET).read_bytes(), NEW)

    def test_current_reviewed_binary_requires_no_transaction(self):
        self.w.path(TARGET).write_bytes(NEW)
        self.w.publish_process()
        self.assertEqual(self.update(), 0)
        self.assertFalse(self.w.path(N.JOURNAL).exists())
        self.assertFalse(self.w.path(GUARD).exists())
        self.assertFalse(any(call[:2] == ['systemctl', 'start'] or call[:2] == ['systemctl', 'stop']
                             for call in self.w.calls))

    def test_target_with_old_ipc_rolls_back_to_original_api40_process(self):
        self.w.hello_apis[NEW] = 40
        self.assertEqual(self.update(), 5)
        self.assertEqual(self.w.path(TARGET).read_bytes(), OLD)
        self.assertEqual(self.journal()['phase'], 'rolled_back')
        self.assertFalse(self.journal()['active'])
        self.assertEqual(self.w.hello_seen[-2:], [(OLD, 'commissioning', 40), (OLD, 'motion', 40)])

    def test_previous_reviewed_binary_rollback_preserves_api40_readiness(self):
        self.w.path(TARGET).write_bytes(PREVIOUS)
        self.w.publish_process()
        self.w.hello_apis[NEW] = 40
        self.assertEqual(self.update(), 5)
        self.assertEqual(self.w.path(TARGET).read_bytes(), PREVIOUS)
        self.assertEqual(self.journal()['phase'], 'rolled_back')
        self.assertEqual(self.w.hello_seen[-2:], [(PREVIOUS, 'commissioning', 40), (PREVIOUS, 'motion', 40)])

    def test_startup46_binary_can_upgrade_to_current_api44(self):
        self.w.path(TARGET).write_bytes(STARTUP)
        self.w.publish_process()
        self.assertEqual(self.update(), 0)
        self.assertEqual(self.journal()['old_sha256'], SHA(STARTUP))
        self.assertEqual(self.w.hello_seen, [(NEW, 'commissioning', 44), (NEW, 'motion', 44)])

    def test_published47_binary_upgrade_and_rollback_require_exact_api42(self):
        self.w.path(TARGET).write_bytes(PUBLISHED);self.w.publish_process()
        self.w.hello_apis[NEW]=42
        self.assertEqual(self.update(),5)
        self.assertEqual(self.journal()['phase'],'rolled_back')
        self.assertEqual(self.w.hello_seen[-2:],[(PUBLISHED,'commissioning',42),(PUBLISHED,'motion',42)])

    def test_api42_target_cannot_be_mistaken_for_api44(self):
        self.w.hello_apis[NEW]=42
        self.assertEqual(self.update(),5)
        self.assertEqual(self.journal()['phase'],'rolled_back')

    def test_startup46_rollback_requires_original_api41_readiness(self):
        self.w.path(TARGET).write_bytes(STARTUP)
        self.w.publish_process()
        self.w.hello_apis[NEW] = 41
        self.assertEqual(self.update(), 5)
        self.assertEqual(self.journal()['phase'], 'rolled_back')
        self.assertFalse(self.journal()['active'])
        self.assertEqual(self.w.path(TARGET).read_bytes(), STARTUP)
        self.assertEqual(self.w.hello_seen[-2:], [(STARTUP, 'commissioning', 41), (STARTUP, 'motion', 41)])

    def test_startup46_rollback_wrong_api40_retains_guard_and_recovery(self):
        self.w.path(TARGET).write_bytes(STARTUP)
        self.w.publish_process()
        self.w.hello_apis.update({NEW: 41, STARTUP: 40})
        self.assertEqual(self.update(), 5)
        self.assertEqual(self.journal()['phase'], 'recovery_required')
        self.assertTrue(self.journal()['active'])
        self.assertFalse(self.journal()['running_verified'])
        self.assertEqual(self.w.path(TARGET).read_bytes(), STARTUP)
        self.assertTrue(self.w.path(GUARD).exists())

    def test_rollback_wrong_ipc_retains_guard_and_recovery_required(self):
        self.w.hello_apis.update({NEW: 40, OLD: 41})
        self.assertEqual(self.update(), 5)
        self.assertEqual(self.w.path(TARGET).read_bytes(), OLD)
        self.assertEqual(self.journal()['phase'], 'recovery_required')
        self.assertTrue(self.journal()['active'])
        self.assertFalse(self.journal()['running_verified'])
        self.assertTrue(self.w.path(GUARD).exists())

    def legacy_terminal(self, phase='completed', old=OLD, target=PREVIOUS):
        self.assertEqual(self.update(), 0)
        record = self.journal()
        installed = target if phase == 'completed' else old
        record.update(phase=phase, completion=phase, old_sha256=SHA(old), target_sha256=SHA(target),
                      installed_sha256=SHA(installed), commit_sha256=SHA(installed))
        record['original_process']['sha256'] = SHA(old)
        record['verified_process']['sha256'] = SHA(installed)
        self.w.path(STATE + '/' + record['transaction_id'] + '/robotd').write_bytes(old)
        raw = (json.dumps(record) + '\n').encode()
        self.w.path(N.JOURNAL).write_bytes(raw)
        self.w.path(TARGET).write_bytes(installed)
        self.w.publish_process()
        return record, raw

    def legacy_identity(self):
        return mock.patch.multiple(N, LEGACY_TARGET_SHA256=SHA(PREVIOUS), LEGACY_OLD_SHA256=SHA(OLD))

    def legacy_readiness(self, raw):
        def readiness(path, payload):
            self.assertEqual(self.w.path(N.JOURNAL).read_bytes(), raw,
                             'old terminal journal must remain intact through readiness')
            return {'ready': True} if payload == {'op': 'readiness'} else {'ok': True, 'status': {'listening': False, 'music': False}}
        return mock.patch.object(N, 'json_socket', side_effect=readiness)

    def assert_legacy_upgrade(self, phase, old=OLD, target=PREVIOUS):
        record, raw = self.legacy_terminal(phase, old, target)
        history = STATE + '/' + record['transaction_id'] + '/journal.json'
        audit = {self.w.path(H.LAST), self.w.path(H.OPLOG)}
        files = {str(p): p.read_bytes() for p in Path(self.tmp).rglob('*') if p.is_file() and p not in audit}
        with self.legacy_identity():
            try:
                self.assertEqual(self.update('status'), 0)
            except H.Stop as error:
                self.fail('clean known predecessor journal refused: ' + error.result)
            self.assertEqual({str(p): p.read_bytes() for p in Path(self.tmp).rglob('*')
                              if p.is_file() and p not in audit}, files)
            self.assertFalse(self.w.path(history).exists())
            with self.legacy_readiness(raw):
                self.assertEqual(self.update(), 0)
        self.assertEqual(self.w.path(history).read_bytes(), raw)
        self.assertNotEqual(self.journal()['transaction_id'], record['transaction_id'])
        self.assertEqual(self.journal()['target_sha256'], SHA(NEW))
        self.assertEqual(self.w.path(TARGET).read_bytes(), NEW)

    def test_legacy_completed45_status_is_read_only_then_upgrade_preserves_history(self):
        self.assert_legacy_upgrade('completed')

    def test_legacy_rolled_back44_can_upgrade_and_preserves_history(self):
        self.assert_legacy_upgrade('rolled_back')

    def test_legacy_completed46_from44_status_and_upgrade_preserve_history(self):
        self.assert_legacy_upgrade('completed', OLD, STARTUP)

    def test_legacy_completed46_from45_status_and_upgrade_preserve_history(self):
        self.assert_legacy_upgrade('completed', PREVIOUS, STARTUP)

    def test_legacy_rolled_back46_to44_status_and_upgrade_preserve_history(self):
        self.assert_legacy_upgrade('rolled_back', OLD, STARTUP)

    def test_legacy_rolled_back46_to45_status_and_upgrade_preserve_history(self):
        self.assert_legacy_upgrade('rolled_back', PREVIOUS, STARTUP)

    def test_legacy_completed47_from44_preserves_history(self):
        self.assert_legacy_upgrade('completed',OLD,PUBLISHED)

    def test_legacy_completed47_from45_preserves_history(self):
        self.assert_legacy_upgrade('completed',PREVIOUS,PUBLISHED)

    def test_legacy_completed47_from46_preserves_history(self):
        self.assert_legacy_upgrade('completed',STARTUP,PUBLISHED)

    def test_legacy_rolled_back47_to44_preserves_history(self):
        self.assert_legacy_upgrade('rolled_back',OLD,PUBLISHED)

    def test_legacy_rolled_back47_to45_preserves_history(self):
        self.assert_legacy_upgrade('rolled_back',PREVIOUS,PUBLISHED)

    def test_legacy_rolled_back47_to46_preserves_history(self):
        self.assert_legacy_upgrade('rolled_back',STARTUP,PUBLISHED)

    def test_legacy47_archive_power_cut_is_idempotent(self):
        self.assert_legacy_archive_power_cut(STARTUP,PUBLISHED)

    def assert_legacy_archive_power_cut(self, old=OLD, target=PREVIOUS):
        record, raw = self.legacy_terminal(old=old, target=target)
        history = STATE + '/' + record['transaction_id'] + '/journal.json'
        original = H.write_atomic
        def cut(ctx, path, data, *args, **kwargs):
            result = original(ctx, path, data, *args, **kwargs)
            if path == history: raise PowerCut()
            return result
        with self.legacy_identity(), self.legacy_readiness(raw):
            with mock.patch.object(H, 'write_atomic', side_effect=cut), self.assertRaises(PowerCut):
                self.update()
            self.assertEqual(self.w.path(N.JOURNAL).read_bytes(), raw)
            self.assertEqual(self.w.path(history).read_bytes(), raw)
            self.assertEqual(self.w.path(TARGET).read_bytes(), target)
            self.assertFalse(self.w.path(GUARD).exists())
            self.assertEqual(self.update(), 0)
        self.assertEqual(self.w.path(history).read_bytes(), raw)

    def test_legacy_archive_power_cut_preserves_journal_and_retry_is_idempotent(self):
        self.assert_legacy_archive_power_cut()

    def test_legacy46_archive_power_cut_preserves_journal_and_retry_is_idempotent(self):
        self.assert_legacy_archive_power_cut(PREVIOUS, STARTUP)

    def test_legacy_foreign_history_is_never_overwritten(self):
        record, raw = self.legacy_terminal()
        history = STATE + '/' + record['transaction_id'] + '/journal.json'
        self.w.path(history).write_bytes(b'foreign history')
        with self.legacy_identity(), self.legacy_readiness(raw), self.assertRaises(H.Stop):
            self.update()
        self.assertEqual(self.w.path(history).read_bytes(), b'foreign history')
        self.assertEqual(self.w.path(N.JOURNAL).read_bytes(), raw)
        self.assertEqual(self.w.path(TARGET).read_bytes(), PREVIOUS)
        self.assertFalse(self.w.path(GUARD).exists())

    def test_legacy_existing_archive_must_sync_before_journal_replacement(self):
        record, raw = self.legacy_terminal()
        directory = STATE + '/' + record['transaction_id']
        self.w.path(directory + '/journal.json').write_bytes(raw)
        original = N.Transaction.sync_dir
        def sync(transaction, path):
            if path == directory: raise OSError('archive persistence failed')
            return original(transaction, path)
        with self.legacy_identity(), self.legacy_readiness(raw):
            with mock.patch.object(N.Transaction, 'sync_dir', sync), self.assertRaises(OSError):
                self.update()
        self.assertEqual(self.w.path(N.JOURNAL).read_bytes(), raw)
        self.assertEqual(self.w.path(TARGET).read_bytes(), PREVIOUS)
        self.assertFalse(self.w.path(GUARD).exists())

    def assert_legacy_refusals(self, old=OLD, target=PREVIOUS):
        record, _ = self.legacy_terminal(old=old, target=target)
        cases = [({'schema': 'foreign'}, None), ({'old_sha256': SHA(NEW)}, None),
                 ({'old_sha256': SHA(STARTUP)}, None),
                 ({'old_sha256': SHA(PREVIOUS), 'target_sha256': SHA(PREVIOUS)}, None),
                 ({'target_sha256': SHA(OLD)}, None), ({'active': True}, None),
                 ({'restore_pending': True}, None), ({'cleanup_pending': True}, None),
                 ({'phase': 'prepared'}, None), ({'phase': 'old_restored'}, None),
                 ({'completion': 'rolled_back'}, None), ({'installed_sha256': SHA(OLD)}, None),
                 ({'commit_sha256': SHA(OLD)}, None), ({'running_verified': False}, None),
                 ({'files': [{**row, 'sha256': 'invalid'} for row in record['files']]}, None)]
        cases += [({}, path) for path in [N.ACTIVE, N.RESTORE_PERMIT, GUARD,
                   *N.Transaction(H, self.op, self.release).controller_guard_paths()]]
        with self.legacy_identity():
            for fields, marker in cases:
                with self.subTest(fields=fields, marker=marker):
                    raw = json.dumps({**record, **fields}).encode()
                    self.w.path(N.JOURNAL).write_bytes(raw)
                    if marker:
                        self.w.path(marker).parent.mkdir(parents=True, exist_ok=True)
                        self.w.path(marker).write_bytes(b'preserve marker')
                    before = list(self.w.calls)
                    for action in ('status', 'install', 'recover'):
                        with self.assertRaises(H.Stop): self.update(action)
                    self.assertEqual(self.w.calls, before)
                    self.assertEqual(self.w.path(N.JOURNAL).read_bytes(), raw)
                    self.assertEqual(self.w.path(TARGET).read_bytes(), target)
                    if marker:
                        self.assertEqual(self.w.path(marker).read_bytes(), b'preserve marker')
                        self.w.path(marker).unlink()
            self.w.path(N.JOURNAL).write_text(json.dumps(record))
            self.w.path(TARGET).write_bytes(OLD)
            self.w.publish_process()
            for action in ('status', 'install', 'recover'):
                with self.assertRaises(H.Stop): self.update(action)

    def test_legacy_tampered_incomplete_or_inconsistent_state_never_mutates(self):
        self.assert_legacy_refusals()

    def test_legacy46_tampered_incomplete_or_inconsistent_state_never_mutates(self):
        self.assert_legacy_refusals(PREVIOUS, STARTUP)

    def test_legacy_incomplete_record_requires_recovery_using_previous_updater(self):
        record, _ = self.legacy_terminal()
        record.update(active=True, phase='old_restored')
        self.w.path(N.JOURNAL).write_text(json.dumps(record))
        with self.legacy_identity(), self.assertRaises(H.Stop) as error:
            self.update('recover')
        self.assertEqual(error.exception.result, 'native_recovery_required')
        self.assertIn('.45', str(error.exception))

    def test_legacy46_incomplete_record_requires_original_signed_updater(self):
        record, _ = self.legacy_terminal(old=PREVIOUS, target=STARTUP)
        record.update(active=True, phase='old_restored')
        self.w.path(N.JOURNAL).write_text(json.dumps(record))
        with self.legacy_identity(), self.assertRaises(H.Stop) as error:
            self.update('recover')
        self.assertEqual(error.exception.result, 'native_recovery_required')
        self.assertIn('.46', str(error.exception))

    def test_legacy47_incomplete_record_requires_original_signed_updater(self):
        record,_=self.legacy_terminal(old=STARTUP,target=PUBLISHED)
        record.update(active=True,phase='old_restored')
        self.w.path(N.JOURNAL).write_text(json.dumps(record))
        with self.assertRaises(H.Stop) as error:self.update('recover')
        self.assertEqual(error.exception.result,'native_recovery_required')
        self.assertIn('.47',str(error.exception))

    def test_fixed_arguments_reject_arbitrary_paths_and_confirmation(self):
        for args in ({'action':'shell'}, {'action':'install'}, {'action':'recover','confirmed':1},
                     {'action':'install','confirmed':True,'path':'/tmp/robotd'}, {'action':'status','by':'evil'}):
            with self.subTest(args=args), self.assertRaises(H.Stop):
                N.handle(H,self.op,{'by':'Studio',**args},self.release)
        self.assertEqual(self.w.path(TARGET).read_bytes(),OLD)

    def test_unknown_elf_exec_running_bytes_and_stale_feedback_refuse_before_mutation(self):
        for mutate in ('unknown_binary','foreign_exec','running_old','stale','torque_unknown'):
            with self.subTest(mutate=mutate):
                with tempfile.TemporaryDirectory() as _:
                    self.w.path(TARGET).write_bytes(OLD)
                    self.w.publish_process()
                    if mutate=='unknown_binary': self.w.path(TARGET).write_bytes(elf(b'foreign'))
                    if mutate=='foreign_exec': self.w.exec_override = ' '.join(ARGS).replace('/run/robotd.sock','/tmp/foreign')
                    if mutate=='running_old':
                        p=self.w.path('/proc/'+str(self.w.pid)+'/exe');p.unlink();p.write_bytes(elf(b'other'))
                    if mutate=='stale': self.w.bus_mutate=lambda b:(self.fresh(b),b['native']['joints'][0].update(sample_age_ms=999))
                    if mutate=='torque_unknown': self.w.robot.torque=None
                    with self.assertRaises(H.Stop): self.update()
                    self.assertFalse(self.w.path(GUARD).exists())
                    self.assertFalse(self.w.path(STATE+'/journal.json').exists())
                    self.w.exec_override=' '.join(ARGS);self.w.bus_mutate=self.fresh;self.w.robot.torque=False

    def test_start_failure_leaves_guard_and_recover_restores_old_before_normal(self):
        self.w.fail='start'
        self.assertEqual(self.update(),5)
        self.assertTrue(self.journal()['active'])
        self.assertTrue(self.w.path(GUARD).exists())
        self.w.fail=None
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.w.path(TARGET).read_bytes(),OLD)
        self.assertFalse(self.journal()['active'])
        self.assertEqual(self.update('recover'),0)

    def test_user_changes_after_interruption_are_never_overwritten_or_resumed(self):
        self.w.fail='start';self.update();self.w.fail=None
        changed = b'# later user change\n'
        self.w.path(H.CALIB_SAVED).write_bytes(changed)
        self.assertEqual(self.update('recover'),5)
        self.assertEqual(self.w.path(H.CALIB_SAVED).read_bytes(),changed)
        self.assertTrue(self.w.path(GUARD).exists())
        self.assertTrue(self.journal()['active'])

    def test_helper_trust_rejects_source_tree_before_loading_module(self):
        self.assertTrue(hasattr(H,'load_native_update'),'missing managed release loader')
        with self.assertRaises(H.Stop) as error:
            H.load_native_update(self.op)
        self.assertEqual(error.exception.result,'native_untrusted_release')

    def test_helper_loads_only_hash_bound_module_from_pinned_managed_release(self):
        self.assertTrue(hasattr(H,'load_native_update'),'missing managed release loader')
        helper = self.w.path(self.release+'/studio/deploy/microduck-helper')
        helper.write_bytes(Path(H.__file__).read_bytes())
        module = self.w.path(self.release+'/studio/deploy/native_update.py')
        module.write_bytes(MODULE.read_bytes())
        suite = self.w.path(self.release+'/suite-version.json')
        names = ('studio/deploy/microduck-helper','studio/deploy/native_update.py','studio/deploy/native/robotd','studio/deploy/native/manifest.json')
        suite.write_text(json.dumps({'version':'2026.10.28','files':{name:SHA(self.w.path(self.release+'/'+name).read_bytes()) for name in names}}),encoding='utf-8')
        current=self.w.path('/opt/microduck/software/current')
        try: current.symlink_to('releases/2026.10.28',target_is_directory=True)
        except OSError: self.skipTest('symlink support required for installed release path')
        with mock.patch.object(H,'__file__',str(helper)):
            loaded, pinned = H.load_native_update(self.op)
            self.assertEqual(pinned,self.release)
            self.assertEqual(loaded.SOURCE,N.SOURCE)
            module.write_bytes(b'raise RuntimeError("must never execute")')
            with self.assertRaises(H.Stop) as error: H.load_native_update(self.op)
            self.assertEqual(error.exception.result,'native_untrusted_release')

    def test_bridge_fixed_verb_and_fail_closed_persistent_state(self):
        from studio import helper
        self.assertTrue(hasattr(helper,'robotd_update'),'missing native bridge')
        with mock.patch.object(helper,'_call',return_value=(True,'native_status',{})) as call:
            helper.robotd_update('Studio','install',confirmed=True)
        self.assertEqual(call.call_args.args[:2],('robotd-update',{'by':'Studio','action':'install','confirmed':True}))
        self.assertFalse(helper.native_update_state(root=self.tmp)['active'])
        p=self.w.path(STATE+'/journal.json');p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'{broken')
        self.assertTrue(helper.native_update_state(root=self.tmp)['active'])

    def test_optional_saved_calibration_absence_is_preserved(self):
        self.w.path(H.CALIB_SAVED).unlink()
        self.assertEqual(self.update(),0)
        self.assertFalse(self.w.path(H.CALIB_SAVED).exists())
        self.assertFalse(self.journal()['files'][3]['existed'])

    def test_later_created_saved_calibration_blocks_recovery_without_overwrite(self):
        self.w.path(H.CALIB_SAVED).unlink();self.w.fail='start';self.update();self.w.fail=None
        self.w.path(H.CALIB_SAVED).write_bytes(b'# created later')
        self.assertEqual(self.update('recover'),5)
        self.assertEqual(self.w.path(H.CALIB_SAVED).read_bytes(),b'# created later')
        self.assertTrue(self.journal()['active'])

    def test_reviewed_payload_accepts_aarch64_pie_but_rejects_other_elf(self):
        self.assertTrue(N.is_aarch64(NEW[:16]+b'\x03\x00'+NEW[18:]))
        self.assertFalse(N.is_aarch64(NEW[:18]+b'\x3e\x00'+NEW[20:]))

    def test_two_replayed_motion_snapshots_do_not_prove_freshness(self):
        self.w.bus_mutate=lambda bus:(self.fresh(bus),bus.update(cycle=1))
        with self.assertRaises(H.Stop): self.update()
        self.assertFalse(self.w.path(GUARD).exists())

    def test_precontrol_lands_in_owned_factory_flow_with_controllers_stopped(self):
        original=self.w.bus_mutate
        self.w.bus_mutate=lambda bus: (original(bus),bus.update(phase='waiting_for_bus')) if self.w.pid==200 else original(bus)
        self.assertEqual(self.update(),0)
        self.assertEqual(self.journal()['mode'],'startup')
        self.assertTrue(self.w.path(H.COMMISSIONING_DROPIN).exists())
        self.assertFalse(self.w.path(GUARD).exists())
        self.assertEqual(self.w.live_mode,'commissioning')
        self.assertTrue(all(self.w.units.get(unit)=='inactive' for unit in N.CONTROLLERS))

    def test_foreign_guard_is_preserved_during_recovery(self):
        self.w.fail='start';self.update();self.w.fail=None
        self.w.path(GUARD).write_bytes(b'# foreign guard')
        self.assertEqual(self.update('recover'),5)
        self.assertEqual(self.w.path(GUARD).read_bytes(),b'# foreign guard')

    def test_protected_service_baseline_rejects_foreign_preexisting_generation(self):
        p=self.w.path(N.PROTECTED);p.write_text(json.dumps({'robotd':{},'padd':{}}))
        with self.assertRaises(H.Stop): self.update()
        self.assertFalse(self.w.path(STATE+'/journal.json').exists())

    def test_transaction_refreshes_only_owned_protected_service_generations(self):
        transaction=N.Transaction(H,self.op,self.release)
        before={'robotd':transaction.unit('robotd'),'padd':transaction.unit('padd')}
        self.w.path(N.PROTECTED).write_text(json.dumps(before),encoding='utf-8')
        self.assertEqual(self.update(),0)
        after=json.loads(self.w.path(N.PROTECTED).read_bytes())
        self.assertNotEqual(after['robotd'],before['robotd'])
        self.assertEqual(after['robotd'],transaction.unit('robotd'))

    def test_reboot_immediately_after_owned_baseline_refresh_is_recoverable(self):
        t=N.Transaction(H,self.op,self.release)
        self.w.path(N.PROTECTED).write_text(json.dumps({'robotd':t.unit('robotd'),'padd':t.unit('padd')}))
        original=H.write_atomic
        def write(ctx,path,data,*args,**kwargs):
            value=original(ctx,path,data,*args,**kwargs)
            if path==N.PROTECTED:raise PowerCut()
            return value
        with mock.patch.object(H,'write_atomic',side_effect=write),self.assertRaises(PowerCut):self.update()
        self.op=H.Op(self.ctx,'robotd-update')
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.w.path(TARGET).read_bytes(),NEW)
        self.assertFalse(self.journal()['active'])

    def test_precontrol_reboot_after_factory_handoff_file_can_recover(self):
        original=self.w.bus_mutate
        self.w.bus_mutate=lambda bus:(original(bus),bus.update(phase='waiting_for_bus')) if self.w.pid==200 else original(bus)
        write=H.write_atomic
        def cut(ctx,path,data,*args,**kwargs):
            result=write(ctx,path,data,*args,**kwargs)
            if path==H.COMMISSIONING_DROPIN:raise PowerCut()
            return result
        with mock.patch.object(H,'write_atomic',side_effect=cut),self.assertRaises(PowerCut):self.update()
        self.op=H.Op(self.ctx,'robotd-update')
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.w.path(TARGET).read_bytes(),OLD)
        self.assertTrue(self.w.path(H.COMMISSIONING_DROPIN).exists())
        self.assertFalse(self.journal()['active'])

    def test_foreign_target_change_during_final_check_does_not_get_restored(self):
        original=self.w.run
        foreign=elf(b'foreign')
        def run(argv):
            if argv==['systemctl','stop','robotd']:self.w.path(TARGET).write_bytes(foreign)
            return original(argv)
        self.w.run=run
        self.assertEqual(self.update(),5)
        self.assertEqual(self.w.path(TARGET).read_bytes(),foreign)
        self.assertTrue(self.journal()['active'])
        self.assertTrue(self.w.path(GUARD).exists())

    def test_mutual_exclusion_refuses_before_own_journal(self):
        for path in (H.SERVICE_LOCK,N.MOTION_LOCK):
            original=self.ctx.lock
            with mock.patch.object(self.ctx,'lock',side_effect=lambda p:None if p==path else original(p)):
                with self.assertRaises(H.Stop) as error:self.update()
                self.assertEqual(error.exception.code,4)
            self.assertFalse(self.w.path(STATE+'/journal.json').exists())

    def test_controller_start_exclusion_persists_after_normal_guard_removal(self):
        remove=H.remove
        def cut(ctx,path):
            result=remove(ctx,path)
            if path==GUARD:raise PowerCut()
            return result
        with mock.patch.object(H,'remove',side_effect=cut),self.assertRaises(PowerCut):self.update()
        self.assertTrue(self.w.path(STATE+'/active').exists(),'persistent controller exclusion missing')
        for unit in N.CONTROLLERS:
            p=self.w.path('/etc/systemd/system/'+unit+'.service.d/zzzzzzz-microduck-native-update.conf')
            self.assertIn('ConditionPathExists=|!'+STATE+'/active',p.read_text(encoding='utf-8'))
        self.op=H.Op(self.ctx,'robotd-update')
        self.assertEqual(self.update('recover'),0)
        self.assertFalse(self.w.path(STATE+'/active').exists())

    def test_persisted_completed_with_pending_controller_restore_is_busy(self):
        original=H.write_atomic
        def cut(ctx,path,data,*args,**kwargs):
            result=original(ctx,path,data,*args,**kwargs)
            if path==N.JOURNAL and json.loads(data).get('phase')=='completed':raise PowerCut()
            return result
        with mock.patch.object(H,'write_atomic',side_effect=cut),self.assertRaises(PowerCut):self.update()
        from studio import helper
        self.assertTrue(helper.native_update_state(root=self.tmp)['active'])
        self.assertTrue(self.journal()['restore_pending'])

    def test_pending_controller_restore_keeps_persistent_block_until_final_record(self):
        original=self.w.run
        def run(argv):
            if argv==['systemctl','start','padd']:
                self.assertTrue(self.w.path(STATE+'/active').exists(),'persistent exclusion removed too early')
                self.assertTrue(self.w.path('/run/microduck-helper/native-update-restore').exists(),'no boot-local restore permit')
                raise PowerCut()
            return original(argv)
        self.w.run=run
        with self.assertRaises(PowerCut):self.update()
        self.assertTrue(self.journal()['restore_pending'])

    def test_true_reboot_after_owned_baseline_refresh_can_recover_from_lost_run_state(self):
        t=N.Transaction(H,self.op,self.release)
        self.w.path(N.PROTECTED).write_text(json.dumps({'robotd':t.unit('robotd'),'padd':t.unit('padd')}))
        original=H.write_atomic
        def cut(ctx,path,data,*args,**kwargs):
            result=original(ctx,path,data,*args,**kwargs)
            if path==N.PROTECTED:raise PowerCut()
            return result
        with mock.patch.object(H,'write_atomic',side_effect=cut),self.assertRaises(PowerCut):self.update()
        self.w.reboot();self.op=H.Op(self.ctx,'robotd-update')
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.w.path(TARGET).read_bytes(),NEW)
        self.assertEqual(json.loads(self.w.path(N.PROTECTED).read_bytes())['robotd'],t.unit('robotd'))

    def test_same_boot_missing_protected_baseline_does_not_mask_foreign_changes(self):
        t=N.Transaction(H,self.op,self.release)
        self.w.path(N.PROTECTED).write_text(json.dumps({'robotd':t.unit('robotd'),'padd':t.unit('padd')}))
        hook=self.w.run
        def run(argv):
            if argv==['systemctl','start','padd'] and self.w.path(N.PROTECTED).exists():self.w.path(N.PROTECTED).unlink()
            return hook(argv)
        self.w.run=run
        self.assertEqual(self.update(),5)
        self.assertTrue(self.journal()['active'])

    def test_fixed_helper_verb_survives_detached_execution(self):
        ctx=CalibCtx(self.w,json.dumps({'by':'Studio','action':'install','confirmed':True}).encode())
        with mock.patch.object(H,'load_native_update',return_value=(N,self.release)):
            self.assertEqual(H.run(['microduck-helper','robotd-update'],ctx),0)
        self.assertEqual(self.w.relays[0][0][-2:],['/usr/local/sbin/microduck-helper','robotd-update'])
        self.assertEqual(self.w.path(TARGET).read_bytes(),NEW)

    @unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'POSIX root ownership test')
    def test_root_owned_transaction_exposes_only_public_record_to_unprivileged_user(self):
        self.ctx.check_owner=True
        self.assertEqual(self.update(),0)
        os.chmod(self.tmp,0o755)
        script=('import json,os,sys;sys.path.insert(0,'+repr(str(MODULE.parents[1]))+');'
                'from studio import helper;'
                's=helper.native_update_state(root='+repr(self.tmp)+');'
                'print(json.dumps({"state":s,"write":os.access('+repr(str(self.w.path(N.JOURNAL)))+',os.W_OK),'
                '"backup":os.access('+repr(str(self.w.path(STATE+'/'+self.journal()['transaction_id']+'/robotd')))+',os.R_OK)}))')
        result=subprocess.run(['/usr/sbin/runuser','-u','nobody','--','/usr/bin/python3.11','-I','-c',script],capture_output=True,text=True,check=True)
        public=json.loads(result.stdout)
        self.assertFalse(public['state']['active'])
        self.assertFalse(public['write']);self.assertFalse(public['backup'])

    @unittest.skipUnless(os.name=='posix','POSIX permissions test')
    def test_writable_or_symlinked_ancestry_is_refused_before_execution(self):
        self.ctx.check_owner=True
        p=self.w.path('/opt/robot/feetech-ft5-r5');os.chmod(p,0o777)
        with self.assertRaises(H.Stop):self.update()
        self.assertFalse(self.w.path(STATE+'/journal.json').exists())

    def test_runtime_status_has_no_backup_or_user_file_contents(self):
        self.update()
        self.assertEqual(self.update('status'),0)
        status=self.op.lines[-1]['detail']
        self.assertNotIn('files',status);self.assertNotIn('protected',status)
        self.assertNotIn('preserve exact file',json.dumps(status))
        self.assertLess(len(json.dumps(status)),2000)

    def test_status_distinguishes_payload_availability_from_unsupported_installation(self):
        self.w.path(TARGET).write_bytes(elf(b'foreign'))
        self.update('status')
        status=self.op.lines[-1]['detail']
        self.assertTrue(status['available'])
        self.assertFalse(status['running_verified'])
        self.assertTrue(status.get('reason'))

    def test_malformed_terminal_state_and_unsafe_binary_mode_refuse(self):
        self.update()
        from studio import helper
        m=self.journal();m['restore_pending']='false'
        self.w.path(N.JOURNAL).write_text(json.dumps(m),encoding='utf-8')
        self.assertTrue(helper.native_update_state(root=self.tmp)['active'])
        with self.assertRaises(H.Stop):self.update('recover')

    @unittest.skipUnless(os.name=='posix','POSIX executable permission test')
    def test_non_executable_known_binary_refuses_before_mutation(self):
        os.chmod(self.w.path(TARGET),0o644)
        with self.assertRaises(H.Stop):self.update()
        self.assertFalse(self.w.path(N.JOURNAL).exists())

    def test_other_helper_control_verbs_refuse_persistent_native_transaction(self):
        self.w.path(N.ACTIVE).parent.mkdir(parents=True,exist_ok=True)
        self.w.path(N.ACTIVE).write_text('owned-active')
        for verb,payload in [('bus-take',{'by':'Studio'}),('bus-give-back',{'by':'Studio','soft':True}),
                             ('calib-commit',{'by':'Studio'}),('imu-recalibrate',{'by':'Studio','action':'enter','confirmed':True})]:
            ctx=NativeCtx(self.w,json.dumps(payload).encode())
            self.assertEqual(H.run(['microduck-helper',verb],ctx),3)
            self.assertEqual(ctx.stdout.lines()[-1]['detail']['result'],'native_update_active')

    @unittest.skipUnless(os.name=='posix','POSIX durable directory sync')
    def test_material_directory_sync_failure_cannot_be_silently_accepted(self):
        transaction=N.Transaction(H,self.op,self.release)
        self.assertTrue(hasattr(transaction,'sync_dir'),'native transactions require strict directory fsync')
        with mock.patch.object(N.os,'fsync',side_effect=OSError('injected disk failure')):
            with self.assertRaises(OSError):transaction.sync_dir('/var/lib')

    def test_fresh_boot_creates_only_fixed_missing_service_lock(self):
        self.w.path(H.SERVICE_LOCK).unlink()
        try:result=self.update()
        except FileNotFoundError:self.fail('fresh boot must allow the fixed service-management lock to be created')
        self.assertEqual(result,0)
        self.assertTrue(self.w.path(H.SERVICE_LOCK).exists())

    def test_missing_controller_motion_lock_is_not_replaced_by_a_different_lock(self):
        self.w.path(N.MOTION_LOCK).unlink()
        with self.assertRaises(OSError):self.update()
        self.assertFalse(self.w.path(N.JOURNAL).exists())

    def test_controller_transitions_and_unknown_states_refuse_before_journal_or_binary_mutation(self):
        for state in ('activating','deactivating','reloading','unknown',''):
            with self.subTest(state=state):
                self.w.units['padd']=state
                with self.assertRaises(H.Stop):self.update()
                self.assertFalse(self.w.path(N.JOURNAL).exists())
                self.assertFalse(self.w.path(GUARD).exists())
                self.assertEqual(self.w.path(TARGET).read_bytes(),OLD)

    def test_optional_not_found_controller_is_recorded_without_start_or_stop(self):
        self.w.absent_units.add('gamesir-xboxd')
        self.w.units['gamesir-xboxd']='inactive'
        self.assertEqual(self.update(),0)
        self.assertEqual(self.journal().get('controller_states',{}).get('gamesir-xboxd'),'absent')
        self.assertFalse(any(call[:2]==['systemctl','stop'] and call[-1]=='gamesir-xboxd' for call in self.w.calls))
        self.assertFalse(any(call[:2]==['systemctl','start'] and call[-1]=='gamesir-xboxd' for call in self.w.calls))

    def test_controller_stop_must_confirm_terminal_state(self):
        original=self.w.run
        def run(argv):
            result=original(argv)
            if argv==['systemctl','stop','padd']:self.w.units['padd']='deactivating'
            return result
        self.w.run=run
        self.assertEqual(self.update(),5)
        self.assertEqual(self.w.path(TARGET).read_bytes(),OLD)
        self.assertTrue(self.journal()['active'])

    def test_inflight_controller_after_exclusion_cannot_pass_final_physical_gate(self):
        original=self.w.bus_mutate
        def bus(status):
            original(status)
            if self.w.path(N.ACTIVE).exists():self.w.units['padd']='activating'
        self.w.bus_mutate=bus
        self.assertEqual(self.update(),5)
        self.assertEqual(self.w.path(TARGET).read_bytes(),OLD)
        self.assertTrue(self.journal()['active'])

    def test_unknown_controller_appearing_after_binary_replace_never_resumes_normal(self):
        original=self.w.run
        def run(argv):
            result=original(argv)
            if argv==['systemctl','start','robotd']:self.w.units['padd']='unknown'
            return result
        self.w.run=run
        self.assertEqual(self.update(),5)
        self.assertEqual(self.w.live_mode,'commissioning')
        self.assertTrue(self.w.path(GUARD).exists())
        self.assertTrue(self.journal()['active'])

    def test_legacy_owned_cleanup_artifacts_recover_when_active_marker_already_removed(self):
        remove=H.remove
        def cut(ctx,path):
            result=remove(ctx,path)
            if path==N.ACTIVE:raise PowerCut()
            return result
        with mock.patch.object(H,'remove',side_effect=cut),self.assertRaises(PowerCut):self.update()
        m=self.journal();m.pop('cleanup_pending',None)
        self.w.path(N.JOURNAL).write_text(json.dumps(m),encoding='utf-8')
        self.w.reboot();self.op=H.Op(self.ctx,'robotd-update')
        self.assertEqual(self.update('recover'),0)
        self.assertFalse(any(self.w.path(p).exists() for p in N.Transaction(H,self.op,self.release).controller_guard_paths()))
        self.assertEqual(self.update('recover'),0)

    def test_cleanup_recovery_preserves_a_foreign_controller_guard(self):
        remove=H.remove
        def cut(ctx,path):
            result=remove(ctx,path)
            if path==N.ACTIVE:raise PowerCut()
            return result
        with mock.patch.object(H,'remove',side_effect=cut),self.assertRaises(PowerCut):self.update()
        path=N.Transaction(H,self.op,self.release).controller_guard_paths()[0]
        self.w.path(path).write_bytes(b'# later foreign guard')
        self.w.reboot();self.op=H.Op(self.ctx,'robotd-update')
        self.assertEqual(self.update('recover'),5)
        self.assertEqual(self.w.path(path).read_bytes(),b'# later foreign guard')
        self.assertTrue(self.journal()['active'])

    def test_cleanup_recovery_refuses_later_user_file_changes_before_controller_restore(self):
        remove=H.remove
        def cut(ctx,path):
            result=remove(ctx,path)
            if path==N.ACTIVE:raise PowerCut()
            return result
        with mock.patch.object(H,'remove',side_effect=cut),self.assertRaises(PowerCut):self.update()
        self.w.reboot();self.op=H.Op(self.ctx,'robotd-update')
        later=b'# later changed calibration'
        self.w.path(H.CALIB).write_bytes(later)
        first=len(self.w.calls)
        self.assertEqual(self.update('recover'),5)
        self.assertEqual(self.w.path(H.CALIB).read_bytes(),later)
        self.assertFalse(any(call[:2]==['systemctl','start'] and call[-1] in N.CONTROLLERS for call in self.w.calls[first:]))
        self.assertTrue(self.journal()['active'])

    def test_cleanup_recovery_refuses_foreign_lifecycle_without_restoring_controllers(self):
        remove=H.remove
        def cut(ctx,path):
            result=remove(ctx,path)
            if path==N.ACTIVE:raise PowerCut()
            return result
        with mock.patch.object(H,'remove',side_effect=cut),self.assertRaises(PowerCut):self.update()
        self.w.reboot();self.op=H.Op(self.ctx,'robotd-update')
        self.w.exec_override=' '.join(ARGS).replace('/run/robotd.sock','/tmp/foreign.sock')
        first=len(self.w.calls)
        self.assertEqual(self.update('recover'),5)
        self.assertFalse(any(call[:2]==['systemctl','start'] and call[-1] in N.CONTROLLERS for call in self.w.calls[first:]))
        self.assertTrue(self.journal()['active'])

    def partial_restore_failure(self,mode='activating',stop_error=None,query_error=False):
        original_run,original_show=self.w.run,self.w.show
        faults={'restore':True,'failed':False,'stop':stop_error,'query':query_error}
        starts=[]
        def run(argv):
            if argv==['systemctl','start','duck-echo'] and faults['restore']:
                self.w.calls.append(list(argv));self.w.units['duck-echo']='activating';faults['failed']=True
                if mode=='timeout':raise subprocess.TimeoutExpired(argv,20)
                return (1 if mode=='nonzero' else 0),'','injected echo start failure'
            if argv==['systemctl','stop','padd'] and faults['failed'] and faults['stop']:
                self.w.calls.append(list(argv))
                if faults['stop']=='exception':raise OSError('injected padd stop exception')
                return 1,'','injected padd stop failure'
            if argv==['systemctl','start','robotd']:
                starts.append('commissioning' if self.w.effective_commissioning else 'motion')
            return original_run(argv)
        def show(unit,props):
            if unit=='padd' and faults['failed'] and faults['query']:raise OSError('injected padd state query failure')
            return original_show(unit,props)
        self.w.run,self.w.show=run,show
        return faults,starts

    def test_partial_restore_stop_failure_does_not_skip_other_units_or_robotd(self):
        faults,_=self.partial_restore_failure('activating',stop_error='nonzero')
        self.assertEqual(self.update(),5)
        echo_failure=next(i for i,call in enumerate(self.w.calls) if call==['systemctl','start','duck-echo'])
        attempted=self.w.calls[echo_failure+1:]
        for unit in (*N.CONTROLLERS,'robotd'):
            self.assertIn(['systemctl','stop',unit],attempted)
        self.assertLess(attempted.index(['systemctl','stop','robotd']),attempted.index(['systemctl','stop','padd']))
        self.assertEqual(self.w.robotd,'inactive')
        self.assertEqual(self.w.units['duck-echo'],'inactive')
        self.assertTrue(self.w.path(GUARD).exists())
        self.assertTrue(self.journal()['active'])
        faults.update(restore=False,stop=None)
        self.assertEqual(self.update('recover'),0)

    def test_partial_restore_stop_exception_does_not_skip_remaining_protection(self):
        self.partial_restore_failure('timeout',stop_error='exception')
        self.assertEqual(self.update(),5)
        self.assertEqual(self.w.robotd,'inactive')
        self.assertEqual(self.w.units['duck-echo'],'inactive')
        self.assertTrue(self.w.path(GUARD).exists())
        self.assertTrue(self.journal()['active'])

    def test_partial_restore_unknown_query_still_stops_all_controllers_and_robotd(self):
        faults,_=self.partial_restore_failure('nonzero',query_error=True)
        self.assertEqual(self.update(),5)
        self.assertTrue(all(self.w.units[unit]=='inactive' for unit in N.CONTROLLERS))
        self.assertEqual(self.w.robotd,'inactive')
        self.assertTrue(self.w.path(GUARD).exists())
        self.assertTrue(self.journal()['active'])
        faults.update(restore=False,query=False)
        self.assertEqual(self.update('recover'),0)

    def test_partial_restore_recovery_requires_fresh_actual_unloaded_runtime(self):
        faults,starts=self.partial_restore_failure()
        self.assertEqual(self.update(),5)
        faults['restore']=False
        original=self.w.bus_mutate
        def unknown_torque(bus):
            original(bus)
            if bus['mode']=='commissioning':bus['torque_state']='unknown'
        self.w.bus_mutate=unknown_torque
        before=len(self.w.calls)
        self.assertEqual(self.update('recover'),5)
        self.assertFalse(any(call[:2]==['systemctl','start'] and call[-1] in N.CONTROLLERS for call in self.w.calls[before:]))
        self.assertTrue(self.w.path(GUARD).exists())
        self.assertTrue(self.journal()['active'])
        self.w.bus_mutate=original
        before_pid=self.w.pid;before_starts=len(starts)
        self.assertEqual(self.update('recover'),0)
        self.assertGreater(self.w.pid,before_pid)
        self.assertEqual(starts[before_starts:],['commissioning','motion'])
        self.assertFalse(self.journal()['active'])

    def test_partial_restore_guard_write_error_cannot_skip_fixed_stop_attempts(self):
        faults,_=self.partial_restore_failure('nonzero')
        original=H.write_atomic
        def write(ctx,path,data,*args,**kwargs):
            if path==GUARD and faults['failed']:raise OSError('injected guard write failure')
            return original(ctx,path,data,*args,**kwargs)
        with mock.patch.object(H,'write_atomic',side_effect=write):self.assertEqual(self.update(),5)
        self.assertEqual(self.w.robotd,'inactive')
        self.assertTrue(all(self.w.units[unit]=='inactive' for unit in N.CONTROLLERS))
        self.assertTrue(self.journal()['active'])
        self.assertEqual(self.w.path(TARGET).read_bytes(),NEW)

    def test_partial_restore_foreign_guard_is_preserved_while_services_are_stopped(self):
        faults,_=self.partial_restore_failure('nonzero')
        original=self.w.run
        foreign=b'# later foreign commissioning guard'
        def run(argv):
            result=original(argv)
            if faults['failed'] and argv==['systemctl','start','duck-echo']:self.w.path(GUARD).write_bytes(foreign)
            return result
        self.w.run=run
        self.assertEqual(self.update(),5)
        self.assertEqual(self.w.path(GUARD).read_bytes(),foreign)
        self.assertEqual(self.w.path(TARGET).read_bytes(),NEW)
        self.assertEqual(self.w.robotd,'inactive')
        self.assertTrue(all(self.w.units[unit]=='inactive' for unit in N.CONTROLLERS))
        self.assertTrue(self.journal()['active'])

    def test_failure_protection_rechecks_robotd_after_all_controller_stops(self):
        faults,_=self.partial_restore_failure('nonzero')
        original=self.w.run
        def run(argv):
            result=original(argv)
            if faults['failed'] and argv==['systemctl','stop','duck-echo']:
                original(['systemctl','start','robotd'])
            return result
        self.w.run=run
        self.assertEqual(self.update(),5)
        self.assertEqual(self.w.path(TARGET).read_bytes(),NEW,'cannot replace a binary after main process reappeared')
        self.assertTrue(self.w.path(GUARD).exists())
        self.assertTrue(self.journal()['active'])


class PowerCut(BaseException):pass


def boundary_test(boundary):
    def test(self):
        original=H.write_atomic
        fired=[]
        def write(ctx,path,data,*args,**kwargs):
            result=original(ctx,path,data,*args,**kwargs)
            match=(path==N.JOURNAL and json.loads(data).get('phase')==boundary)
            match=match or (boundary=='guard_file' and path==GUARD)
            match=match or (boundary=='binary_replace' and path==TARGET and data==NEW)
            if match and not fired:
                fired.append(True);raise PowerCut()
            return result
        with mock.patch.object(H,'write_atomic',side_effect=write),self.assertRaises(PowerCut):self.update()
        self.assertTrue(fired)
        # Simulate process death / reboot: descriptors gone, controllers may boot,
        # durable files and guard remain. Recovery must use real current process.
        self.op=H.Op(self.ctx,'robotd-update')
        self.w.reboot()
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.w.path(TARGET).read_bytes(),NEW if boundary=='completed' else OLD)
        self.assertFalse(self.journal()['active'])
        self.assertTrue(all(self.w.path(p).read_bytes()==v for p,v in self.saved.items()))
    return test


for _boundary in ('prepared','guarding','guard_file','guarded','replacing','binary_replace','replaced',
                  'verified_commissioning','restoring_normal','verified_motion','completed'):
    setattr(NativeTest,'test_reboot_after_'+_boundary,boundary_test(_boundary))


def cleanup_boundary_test(boundary,after_sync=False):
    def test(self):
        t=N.Transaction(H,self.op,self.release)
        paths=[N.ACTIVE]+t.controller_guard_paths()+[N.RESTORE_PERMIT]
        target=paths[boundary]
        fired=[]
        if not after_sync:
            remove=H.remove
            def cut(ctx,path):
                result=remove(ctx,path)
                if path==target and not fired:
                    fired.append(True);raise PowerCut()
                return result
            patch=mock.patch.object(H,'remove',side_effect=cut)
        else:
            sync=N.Transaction.sync_dir
            def cut(transaction,path):
                result=sync(transaction,path)
                m=transaction.load()
                if (m and m.get('completion')=='completed' and m.get('restore_pending') is False
                        and not transaction.present(N.ACTIVE) and not transaction.present(N.RESTORE_PERMIT)
                        and not any(transaction.present(p) for p in transaction.controller_guard_paths()) and not fired):
                    fired.append(True);raise PowerCut()
                return result
            patch=mock.patch.object(N.Transaction,'sync_dir',cut)
        with patch,self.assertRaises(PowerCut):self.update()
        from studio import helper
        self.assertTrue(helper.native_update_state(root=self.tmp)['active'],'cleanup must remain publicly busy')
        self.w.reboot();self.op=H.Op(self.ctx,'robotd-update')
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.w.path(TARGET).read_bytes(),NEW)
        self.assertFalse(any(self.w.path(p).exists() for p in paths))
        self.assertFalse(helper.native_update_state(root=self.tmp)['active'])
        self.assertEqual(self.update('recover'),0)
        self.assertTrue(all(self.w.path(p).read_bytes()==v for p,v in self.saved.items()))
    return test


for _index in range(5):
    setattr(NativeTest,'test_cleanup_reboot_after_unlink_'+str(_index),cleanup_boundary_test(_index))
setattr(NativeTest,'test_cleanup_reboot_after_final_directory_fsync',cleanup_boundary_test(0,after_sync=True))


def partial_restore_test(mode):
    def test(self):
        faults,starts=self.partial_restore_failure(mode)
        self.assertEqual(self.update(),5)
        self.assertTrue(faults['failed'])
        self.assertEqual(self.w.robotd,'inactive')
        self.assertTrue(all(self.w.units[unit]=='inactive' for unit in N.CONTROLLERS))
        self.assertTrue(self.w.path(GUARD).exists())
        self.assertTrue(self.w.path(N.ACTIVE).exists())
        self.assertFalse(self.w.path(N.RESTORE_PERMIT).exists())
        self.assertTrue(self.journal()['active'])
        self.assertFalse(self.journal()['running_verified'])
        self.assertTrue(all(self.w.path(p).read_bytes()==v for p,v in self.saved.items()))
        before_pid=self.w.pid;before_starts=len(starts)
        faults['restore']=False
        self.assertEqual(self.update('recover'),0)
        self.assertGreater(self.w.pid,before_pid)
        self.assertEqual(starts[before_starts:],['commissioning','motion'])
        self.assertEqual(self.w.path(TARGET).read_bytes(),OLD)
        self.assertFalse(self.journal()['active'])
    return test


for _mode in ('activating','nonzero','timeout'):
    setattr(NativeTest,'test_partial_restore_'+_mode+'_failure_leaves_guard_and_stopped_services',partial_restore_test(_mode))


if __name__=='__main__':unittest.main()
