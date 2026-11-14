"""Studio-owned tracking trials sharing ServoSession's bus and operation ledger."""

from collections import deque
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import threading
import time
import tomllib
from types import SimpleNamespace
import uuid

from . import leg_tracking_probe as probe
from .analyze_leg_tracking import analyze
from .servo import Refused


class SessionBus:
    """Bounded packet adapter; emergency-stop latching and writes share _cv."""

    def __init__(self, controller, log, centers, gains, amplitude):
        self.controller, self.log = controller, log
        self.centers, self.gains, self.amplitude = centers, gains, amplitude
        self.cleanup_mode = False

    def emit(self, row):
        try:
            self.log(row)
        except OSError:
            if not self.cleanup_mode:
                raise

    def read(self, sid, address, length):
        probe.require(probe.permitted(sid, 2, bytes([address, length]), self.centers,
                                      self.amplitude, self.gains), 'tracking read rejected')
        self.emit({'type': 'tx', 'id': sid, 'instruction': 2,
                   'params_hex': bytes([address, length]).hex(), 'at': time.monotonic()})
        error, data = self.controller.session.bus.read(sid, address, length)
        self.emit({'type': 'rx', 'id': sid, 'error': error, 'data_hex': data.hex(), 'at': time.monotonic()})
        probe.require(error == 0, f'ID{sid} reply error 0x{error:02x}')
        return error, data

    def read_u8(self, sid, address):
        return self.read(sid, address, 1)[1][0]

    def write(self, sid, address, data):
        params = bytes([address]) + bytes(data)
        probe.require(probe.permitted(sid, 3, params, self.centers, self.amplitude, self.gains),
                      'tracking write rejected')
        session = self.controller.session
        # relax_all sets latched under this same lock before sending torque-off.
        # Therefore no queued test goal/torque-on can follow the emergency stop.
        with session._cv:
            if not self.cleanup_mode:
                self.controller.check_running()
            self.emit({'type': 'tx', 'id': sid, 'instruction': 3,
                       'params_hex': params.hex(), 'at': time.monotonic()})
            error = session.bus.write(sid, address, data)
            self.emit({'type': 'write_ack', 'id': sid, 'error': error, 'at': time.monotonic()})
            return error

    def write_u16(self, sid, address, value):
        return self.write(sid, address, struct.pack('<H', value))

    def state(self, sid):
        error, data = self.read(sid, 56, 15)
        value = self.controller.session.bus._decode_state(error, data)
        self.controller.session.rows[sid] = {**value, 'at': time.monotonic()}
        return value


class TrackingController:
    def __init__(self, session, state_dir, calibration='/etc/robot/feetech-ft5/hd1910-calibration.toml'):
        self.session = session
        self.directory = Path(state_dir or '.') / 'leg-tracking'
        self.calibration = Path(calibration)
        self.active = False
        self.owner = None
        self.lease_at = 0
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.tail = deque(maxlen=240)
        self.centers = {}
        self.state = {'active': False, 'phase': 'idle', 'message': '选择关节，先做原参数基线测试'}

    def check_running(self):
        probe.require(not self.stop_event.is_set(), 'operator stopped the trial')
        probe.require(time.monotonic() - self.lease_at < 2.5, 'page heartbeat expired')
        probe.require(self.session.state == 'open' and not self.session.halted,
                      'session stopped, relaxed or handing back')

    def validate(self, args):
        ids = args.get('ids')
        if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in probe.LEG_IDS for i in ids):
            raise Refused('请选择有效的腿部关节')
        if len(ids) != len(set(ids)):
            raise Refused('关节不能重复')
        amplitude = args.get('amplitude_deg', 3)
        if type(amplitude) not in (int, float) or not math.isfinite(amplitude) or not 1 <= amplitude <= 5:
            raise Refused('测试幅度须为 1–5°')
        if args.get('supported') is not True:
            raise Refused('先确认躯干已架稳、双腿活动范围无遮挡')
        kp, kd = args.get('kp'), args.get('kd')
        for value, low, high, name in ((kp, 1, 32, 'P'), (kd, 0, 64, 'D')):
            if value is not None and (type(value) is not int or not low <= value <= high):
                raise Refused(f'{name} 参数须为 {low}–{high} 的整数')
        if (kp is not None or kd is not None) and len(ids) != 1:
            raise Refused('改 P/D 时每次只测一个关节')
        if type(args.get('pairs', False)) is not bool:
            raise Refused('双腿配合选项无效')
        return SimpleNamespace(ids=ids, amplitude_deg=amplitude, kp=kp, kd=kd, pairs=args.get('pairs', False))

    def _load_calibration(self):
        """Return the parsed calibration, fingerprint bytes, and where they came from.

        Read the file directly when this process can. The installed file is root-only,
        so the studio user falls back to the same root helper the rest of Studio uses.
        That is the main-program file, not a synthesized calibration.
        """
        try:
            raw = self.calibration.read_bytes()
        except OSError as error:
            return self._load_calibration_from_helper(error)
        return tomllib.loads(raw.decode()), raw, 'file'

    def _load_calibration_from_helper(self, error):
        from . import helper
        ok, _result, detail = helper.calib_read(by='关节跟踪测试')
        calibration = detail.get('calibration') if ok and isinstance(detail, dict) else None
        if not isinstance(calibration, dict):
            raise Refused('无法读取主程序的舵机标定，请先完成标定并检查配置') from error
        raw = json.dumps(calibration, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        return calibration, raw, 'helper'

    def start(self, args, owner):
        options = self.validate(args)
        # Read calibration before reserving a running session.
        calibration, raw, source = self._load_calibration()
        with self.session._cv:
            if self.active or self.session._inflight or self.session._busy_locked():
                raise Refused('请先停止动作、灵动、校准和其他舵机操作')
            if self.session.state != 'open' or self.session.closing or self.session.ops is None:
                raise Refused('请先接管舵机总线')
            if not self.session.ops.calib_lock.acquire(blocking=False):
                raise Refused('舵机总线正在执行其他操作')
            token = None
            try:
                token = self.session.op_begin('tracking')
                self.active = True
                self.owner, self.lease_at = owner, time.monotonic()
                self.stop_event.clear()
                self.session.latched = False  # This explicit start is a fresh operator action.
                run_id = uuid.uuid4().hex
                with self.lock:
                    self.tail.clear()
                    self.centers.clear()
                    self.state = {'active': True, 'phase': 'preflight', 'run_id': run_id,
                                  'message': '检查扭矩、标定和整段动作范围', 'sim': self.session.sim}
                self.thread = threading.Thread(target=self._worker,
                                               args=(options, calibration, raw, source, run_id, token),
                                               name='leg-tracking', daemon=True)
                self.thread.start()
            except BaseException:
                self.active = False
                if token is not None:
                    self.session.op_end(token)
                self.session.ops.calib_lock.release()
                raise
        return {'run_id': run_id}

    def _worker(self, options, calibration, raw, source, run_id, token):
        capture = self.directory / f'{run_id}.jsonl'
        result = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with capture.open('x') as output:
                def log(row):
                    output.write(json.dumps(row, allow_nan=False) + '\n')
                    output.flush()
                    with self.lock:
                        if row['type'] == 'before':
                            self.centers[row['id']] = (row['center'], row['joint']['direction'])
                        elif row['type'] == 'stage':
                            self.state.update(phase='running', stage=row['stage'], ids=row['ids'],
                                              message='慢动作跟踪测试进行中')
                        elif row['type'] == 'sample':
                            center, direction = self.centers[row['id']]
                            self.tail.append({'id': row['id'], 'stage': row['stage'], 't': row['at'],
                                              'target': (row['target'] - center) * direction * probe.DEG_PER_RAW,
                                              'actual': (row['state']['pos'] - center) * direction * probe.DEG_PER_RAW})

                stages = probe.build_stages(options.ids, options.amplitude_deg, pairs=options.pairs)
                plan = {'ids': options.ids, 'amplitude_deg': options.amplitude_deg,
                        'kp_override': options.kp, 'kd_override': options.kd, 'stages': len(stages),
                        'nominal_hz': 20}
                log({'type': 'meta', 'schema': 'leg-tracking-v1', 'plan': plan,
                     'wall_time': time.time(), 'sim': self.session.sim,
                     'calibration_sha256': hashlib.sha256(raw).hexdigest(),
                     'calibration_source': source,
                     'profile': 'existing_duration_speed_acceleration_torque_limit_preserved',
                     'current_units_verified': False})
                bus = SessionBus(self, log, {}, {}, math.ceil(options.amplitude_deg / probe.DEG_PER_RAW))
                result = probe.run_test(bus, options, stages, calibration, bus.centers, bus.gains, log)
            rows = [json.loads(line) for line in capture.read_text().splitlines()]
            if any(row['type'] == 'sample' for row in rows):
                report = analyze(rows)
                (self.directory / f'{run_id}.report.json').write_text(json.dumps(report, allow_nan=False))
                with self.lock:
                    self.state['report'] = report
            with self.lock:
                self.state.update(phase='completed' if result['completed'] else 'stopped', result=result,
                                  message='测试完成，已卸力并恢复原 P/D' if result['completed']
                                  else '测试已停止，请查看停止原因和恢复结果')
        except Exception as error:
            with self.lock:
                self.state.update(phase='error', message=str(error))
            self.session.log.error('关节跟踪测试出错', cat='总线', raw=f'{type(error).__name__}: {error}')
        finally:
            # Refresh cached torque/gains before exposing the bus to other controls.
            try:
                self.session._read_torque()
                self.session.refresh_static(options.ids)
            except Exception as error:
                self.session.log.warn('测试后状态回读失败', cat='总线', raw=str(error))
            self.session.latched = True
            self.session.ops.calib_lock.release()
            with self.session._cv:
                self.active = False
                self.session.op_end(token)
            with self.lock:
                self.state['active'] = False

    def snapshot(self, owner):
        if owner == self.owner:
            self.lease_at = time.monotonic()
        with self.lock:
            return {**self.state, 'tail': list(self.tail), 'history': self.history()}

    def history(self):
        rows = []
        for path in sorted(self.directory.glob('*.report.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                report = json.loads(path.read_text())
                rows.append({'run_id': path.name.split('.')[0], 'at': report['capture']['wall_time'],
                             'plan': report['capture']['plan'], 'issues': report['issues'],
                             'sim': report['capture'].get('sim', False)})
            except (OSError, ValueError, KeyError):
                continue
        return rows

    def export_path(self, run_id, kind):
        if not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{32}', run_id):
            raise Refused('记录编号无效')
        suffix = {'capture': '.jsonl', 'report': '.report.json'}.get(kind)
        if suffix is None:
            raise Refused('导出类型无效')
        if self.active and self.state.get('run_id') == run_id:
            raise Refused('请等待测试停止、记录写完后再导出')
        path = self.directory / (run_id + suffix)
        if not path.is_file():
            raise Refused('记录尚未生成')
        return path

    def handle(self, args, owner):
        op = args.get('op')
        if op == 'status':
            return self.snapshot(owner)
        if op == 'start':
            return self.start(args, owner)
        if op == 'stop':
            self.stop_event.set()
            return {'stopping': True}
        if op == 'load':
            return json.loads(self.export_path(args.get('run_id'), 'report').read_text())
        raise Refused('未知的关节调试操作')

    def disconnect(self, owner):
        if owner == self.owner:
            self.stop_event.set()

    def shutdown(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                self.session.relax_all('停止关节跟踪测试')
                raise RuntimeError('关节调试尚未退出，不能关闭总线')
