"""Studio 用户模型库；运行配置的成功以主程序实际加载回读为准。"""
import asyncio
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import uuid
from pathlib import Path

from .model_package import PackageError, extract_package, validate_policy
from .rpc import call_once

SCHEMA = 'microduck-studio-model-library/v1'
META = '.studio-model.json'
BASE = '/opt/robot/feetech-ft5-r5'
PREFLIGHT = BASE + '/robotd'
SLOTS = ('walk', 'sitstand', 'ground_pick', 'kick_left', 'kick_right', 'roulade', 'stand')
LABELS = dict(zip(SLOTS, ('走路 / 保持', '坐下 / 站起', '低头捡东西', '左脚踢', '右脚踢', '前滚翻', '站立（独立）')))
SKILLS = {'sitstand': 'sit_toggle', 'ground_pick': 'ground_pick', 'kick_left': 'kick_left',
          'kick_right': 'kick_right', 'roulade': 'roulade'}
DEFAULT_KEYS = {'a': 'ground_pick', 'x': 'roulade', 'lb': 'kick_left', 'rb': 'kick_right', 'dpad_down': 'sit_toggle'}
BUTTONS = {'a': 'A', 'x': 'X', 'lb': 'LB', 'rb': 'RB', 'dpad_down': '十字下'}
JOINTS = ('left_hip_yaw,left_hip_roll,left_hip_pitch,left_knee,left_ankle,neck_pitch,'
          'head_pitch,head_yaw,head_roll,right_hip_yaw,right_hip_roll,right_hip_pitch,right_knee,right_ankle').split(',')
READBACK_TIMEOUT = 25.0
READBACK_POLL = 0.2


class ModelError(ValueError):
    def __init__(self, message, *, code=None, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def _json(path, limit=8 << 20):
    if path.is_symlink() or path.stat().st_size > limit:
        raise ValueError('文件为链接或超过大小上限')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('需要 JSON 对象')
    return value


def _atomic_json(path, value):
    """同目录临时文件、落盘、原子替换，避免掉电留下半份库记录。"""
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            dfd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _signature(contract):
    """HOME/P 是进程启动时安装的全局量，热换不能改变它们。"""
    try:
        if (type(contract.get('input_dim')) is not int or contract['input_dim'] != 61 or
                type(contract.get('output_dim')) is not int or contract['output_dim'] != 14 or
                contract.get('obs_normalizer') != 'baked_in_onnx'):
            raise ValueError('观测接口必须是已归一化的 61 维输入 / 14 维动作')
        model = contract['model']
        names = contract['onnx_metadata']['joint_names'].split(',')
        if names != JOINTS:
            raise ValueError('关节顺序不同')
        rows = model['joints']
        if not isinstance(rows, list):
            raise ValueError('HOME 关节数量不完整')
        homes = {}
        for name in JOINTS:
            matches = [r for r in rows if isinstance(r, dict) and r.get('name') == name]
            if len(matches) != 1:
                raise ValueError('HOME 关节名称缺失或重复：' + name)
            homes[name] = matches[0]['home_rad']
        if any(isinstance(homes[n], bool) or not isinstance(homes[n], (float, int))
               or not math.isfinite(homes[n]) for n in JOINTS):
            raise ValueError('HOME 含无效角度')
        actuators = model['actuators']
        if not isinstance(actuators, list) or len(actuators) != 1:
            raise ValueError('舵机 P 参数不完整')
        kp = actuators[0]['kp_fw']
        if isinstance(kp, bool) or not isinstance(kp, (int, float)) or not math.isfinite(kp) or int(kp) != kp:
            raise ValueError('舵机 P 参数无效')
        return {'joints': names, 'home': [float(homes[n]) for n in JOINTS], 'kp_fw': int(kp)}
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as exc:
        raise ModelError(f'无法确认模型控制合同（HOME / 舵机 P / 关节顺序）：{exc}') from exc


class Models:
    def __init__(self, rd, log, state_dir, tune=None, root='/', validator=None, rpc=None):
        self.rd, self.log, self.tune = rd, log, tune
        self.root = Path(root)
        self.directory = Path(state_dir) / 'models'
        self.catalog = self.directory / 'library.json'
        self.validator, self.rpc = validator, rpc
        self._lock = asyncio.Lock()
        self._disk_lock = threading.RLock()

    def _local(self, path):
        if not isinstance(path, str) or not path.startswith('/'):
            raise ModelError('主程序模型路径必须是绝对路径')
        return self.root / path.lstrip('/')

    def _remote(self, path):
        path = Path(path).resolve()
        if str(self.root) == '/':
            return str(path)
        try:
            return '/' + path.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            # 用于电脑端独立 state 目录；生产 state 在 /var/lib 下。
            return path.as_posix()

    def _managed(self, path):
        try:
            self._local(path).resolve().relative_to(self.directory.resolve())
            return True
        except (ValueError, ModelError, OSError):
            return False

    def _init_dir(self):
        if self.directory.is_symlink():
            raise ModelError('模型库目录不能是符号链接')
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _name(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80 or any(ord(c) < 32 for c in value):
            raise ModelError('模型名称需为 1–80 个可见字符')
        return value.strip()

    async def _call(self, method, params=None, timeout=2.0):
        if self.rpc is not None:
            return await self.rpc(method, params or {}, timeout=timeout)
        return await call_once(self.rd.addr, method, params, timeout=timeout, name='models', log=self.log)

    async def _snapshot(self):
        names = ('robot.policies', 'robot.busStatus', 'pad.bindings', 'robot.skills')
        values = await asyncio.gather(*(self._call(n) for n in names), return_exceptions=True)
        snap, errors = {}, []
        for name, val in zip(('policies', 'bus', 'bindings', 'skills'), values):
            if self._report_valid(name, val):
                snap[name] = val
            else:
                snap[name] = None
                errors.append(name)
        snap['error'] = '主程序连接或回读失败，暂时无法确认运行配置' if errors else None
        return snap

    @staticmethod
    def _report_valid(name, value):
        """空对象/半份报告不是权威回读，特别不能据此判定模型无人引用。"""
        if not isinstance(value, dict):
            return False
        def path_ok(path):
            return path is None or isinstance(path, str) and path.startswith('/')
        def rows_ok(key, check):
            rows = value.get(key)
            return isinstance(rows, list) and all(isinstance(row, dict) and check(row) for row in rows)
        if name == 'policies':
            if not isinstance(value.get('enabled'), bool) or not isinstance(value.get('skills'), list) or not all(isinstance(s, str) for s in value['skills']):
                return False
            if not rows_ok('slots', lambda r: isinstance(r.get('slot'), str) and r['slot'] in SLOTS and
                           path_ok(r.get('path')) and isinstance(r.get('overridden'), bool)):
                return False
            return sorted(r['slot'] for r in value['slots']) == sorted(SLOTS)
        if name == 'bus':
            lm = value.get('loaded_models')
            return isinstance(value.get('mode'), str) and isinstance(lm, dict) and all(
                isinstance(k, str) and (v is None or isinstance(v, dict) and path_ok(v.get('path'))) for k, v in lm.items())
        if name == 'bindings':
            return rows_ok('bindings', lambda r: isinstance(r.get('button'), str) and r['button'] in BUTTONS and
                           isinstance(r.get('skill'), str) and isinstance(r.get('overridden'), bool)) and sorted(
                               r['button'] for r in value['bindings']) == sorted(BUTTONS)
        if name == 'skills':
            return rows_ok('skills', lambda r: isinstance(r.get('name'), str) and path_ok(r.get('path'))) and isinstance(
                value.get('built_in'), list) and all(isinstance(n, str) for n in value['built_in'])
        return False

    @staticmethod
    def _idle_reason(snap):
        bus, pol = snap.get('bus'), snap.get('policies')
        if not isinstance(bus, dict) or not isinstance(pol, dict):
            return '主程序连接或回读失败，无法确认卸力状态'
        native = bus.get('native')
        if not isinstance(native, dict) or native.get('torque_enabled') is not False or native.get('torque_state_confirmed') is not False:
            return '请先卸力，等待主程序确认已卸力后再修改模型或按键'
        if bus.get('policy_enabled') is not False:
            return '请先停止策略并卸力后再修改模型或按键'
        if bus.get('mode') != 'motion' or pol.get('enabled') is not True:
            return '当前模式未开启模型运行器，请切回运动模式并保持卸力'
        if snap.get('error'):
            return snap['error']
        return None

    async def _require_idle(self):
        snap = await self._snapshot()
        reason = self._idle_reason(snap)
        if reason:
            raise ModelError(reason, status=409, code='not_idle')
        return snap

    @staticmethod
    def _rows(value, key):
        rows = value.get(key) if isinstance(value, dict) else None
        return rows if isinstance(rows, list) else []

    def _load_catalog(self):
        if not self.catalog.exists():
            return {'schema': SCHEMA, 'defaults': {}}, None
        try:
            data = _json(self.catalog, 1 << 20)
            if data.get('schema') != SCHEMA or not isinstance(data.get('defaults'), dict):
                raise ValueError('库格式不匹配')
            for slot, d in data['defaults'].items():
                if slot not in SLOTS or not isinstance(d, dict) or d.get('id') != 'builtin-' + slot:
                    raise ValueError('默认模型记录无效')
                path = d.get('path')
                disabled = d.get('disabled') is True and slot != 'walk' and path is None
                if not disabled and (not isinstance(path, str) or not path.startswith('/') or self._managed(path)):
                    raise ValueError('默认恢复路径无效或指向导入库')
                self._name(d.get('name'))
                if d.get('sha256') is not None and (not isinstance(d['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', d['sha256'])):
                    raise ValueError('默认模型摘要无效')
                sig = d.get('signature')
                if sig is not None:
                    if not isinstance(sig, dict) or sig.get('joints') != JOINTS or not isinstance(sig.get('home'), list) or len(sig['home']) != len(JOINTS):
                        raise ValueError('默认 HOME 合同记录无效')
                    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in sig['home']):
                        raise ValueError('默认 HOME 含无效角度')
                    if type(sig.get('kp_fw')) is not int or not 1 <= sig['kp_fw'] <= 32:
                        raise ValueError('默认舵机 P 记录无效')
            return data, None
        except (OSError, ValueError, RecursionError, OverflowError) as exc:
            return {'schema': SCHEMA, 'defaults': {}}, f'模型库默认记录损坏，已停止配置修改：{exc}'

    def _read_library(self, snap=None):
        with self._disk_lock:
            self._init_dir()
            catalog, error = self._load_catalog()
            if not self.catalog.exists() and snap and not snap.get('error'):
                defaults = self._capture_defaults(snap)
                if defaults:
                    catalog['defaults'] = defaults
                    _atomic_json(self.catalog, catalog)
            entries = []
            for child in sorted(self.directory.iterdir()):
                if not re.fullmatch(r'[a-f0-9]{32}', child.name) or child.is_symlink() or not child.is_dir():
                    continue
                try:
                    entry = _json(child / META, 64 << 10)
                    if entry.get('id') != child.name or entry.get('slot') not in SLOTS or not isinstance(entry.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']):
                        raise ValueError('模型身份记录无效')
                    entry['name'] = self._name(entry.get('name'))
                    entry['path'] = self._remote(child / 'policy.onnx')
                    entry['source'] = 'imported'
                    entry['installed'] = self._local(entry['path']).is_file() and not self._local(entry['path']).is_symlink()
                    entries.append(entry)
                except (ValueError, OSError, RecursionError) as exc:
                    error = error or f'模型 {child.name[:8]} 的记录无法读取：{exc}'
            builtins = [dict(d, source='builtin', slot=s,
                             installed=self._local(d['path']).is_file() and not self._local(d['path']).is_symlink())
                        for s, d in catalog['defaults'].items() if not d.get('disabled')]
            return catalog, builtins + entries, error

    def _capture_defaults(self, snap):
        """优先随镜像的档案；只在首次建立库时记原始显式路径。"""
        candidates, names = {}, {}
        try:
            profile = _json(self._local(BASE + '/management-profile.json'))
            for row in self._rows(profile, 'builtin_models'):
                if not isinstance(row, dict) or row.get('slot') not in SLOTS:
                    continue
                ident = row.get('id')
                if isinstance(ident, str) and re.fullmatch(r'[A-Za-z0-9_-]+', ident):
                    candidates[row['slot']] = BASE + '/models/' + ident + '/policy.onnx'
                    names[row['slot']] = row.get('name')
        except (OSError, ValueError):
            pass
        for row in self._rows(snap.get('policies'), 'slots'):
            if isinstance(row, dict) and row.get('slot') in SLOTS:
                candidates.setdefault(row['slot'], row.get('path'))
        defaults = {}
        tune_names = (getattr(self.tune, 'disk', {}) or {}).get('builtin_names', {})
        for slot, path in candidates.items():
            if not isinstance(path, str) or not path.startswith('/') or self._managed(path):
                continue
            local = self._local(path)
            if not local.is_file() or local.is_symlink():
                continue
            sig, task, sha = None, None, None
            try:
                con = _json(local.with_name('deployment-contract.json'))
                sig, task = _signature(con), con.get('task')
            except (OSError, ValueError):
                pass
            try:
                sha = hashlib.sha256(local.read_bytes()).hexdigest()
            except OSError:
                pass
            defaults[slot] = {'id': 'builtin-' + slot, 'path': path,
                              'name': names.get(slot) or tune_names.get(local.parent.name) or LABELS[slot],
                              'task': task, 'sha256': sha, 'signature': sig}
        for row in self._rows(snap.get('policies'), 'slots'):
            slot = row.get('slot')
            if slot in SLOTS and slot != 'walk' and slot not in defaults and row.get('path') is None:
                defaults[slot] = {'id': 'builtin-' + slot, 'path': None, 'name': LABELS[slot] + '（默认未安装）',
                                  'task': None, 'sha256': None, 'signature': None, 'disabled': True}
        return defaults

    def _validator_available(self):
        return self.validator is not None or self._local(PREFLIGHT).is_file()

    def _runtime_env(self):
        env = dict(os.environ)
        if str(self.root) != '/' or os.name == 'nt':
            return env
        try:
            result = subprocess.run(['systemctl', 'show', 'robotd.service', '--property=Environment', '--value'],
                                    capture_output=True, text=True, timeout=3, shell=False)
            if result.returncode == 0:
                for item in shlex.split(result.stdout):
                    key, sep, value = item.partition('=')
                    if sep and key in ('ORT_DYLIB_PATH', 'LD_LIBRARY_PATH'):
                        env[key] = value
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return env

    def _preflight(self, path):
        if self.validator is not None:
            result = self.validator(path)
        else:
            if not self._validator_available():
                raise ModelError('本机没有可信模型运行器，无法执行 ONNX 预检', status=503)
            result = validate_policy(path, str(self._local(PREFLIGHT)), env=self._runtime_env())
        if not isinstance(result, dict) or result.get('valid') is not True:
            raise ModelError('模型运行器预检未通过：' + str((result or {}).get('error', '未确认有效') if isinstance(result, dict) else '返回无效结果'))

    def _install(self, path, name):
        ident = uuid.uuid4().hex
        stage, final = self.directory / ('.stage-' + ident), self.directory / ident
        try:
            self._init_dir()
            data = extract_package(Path(path), stage)
            self._preflight(stage / data['onnx'])
            slot = data.get('slot')
            if slot not in SLOTS:
                raise ModelError('无法识别这个模型的动作用途，暂不能安装')
            record = {'id': ident, 'name': self._name(name or data.get('name') or LABELS[slot]),
                      'task': data['task'], 'slot': slot, 'sha256': data['sha256'], 'files': data['files']}
            _atomic_json(stage / META, record)
            os.replace(stage, final)
        except (PackageError, OSError, KeyError, TypeError) as exc:
            raise ModelError(f'模型导入失败：{exc}') from exc
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    async def import_package(self, path, name=''):
        if not isinstance(name, str):
            raise ModelError('模型名称必须是文字')
        if name:
            self._name(name)
        async with self._lock:
            snap = await self._snapshot()
            _, _, error = await self._disk(self._read_library, snap)
            if error:
                raise ModelError(error, status=409)
            await asyncio.to_thread(self._install, path, name)
        return await self.view()

    @staticmethod
    def _references(snap):
        paths = set()
        for key, field in (('policies', 'slots'), ('skills', 'skills')):
            for row in Models._rows(snap.get(key), field):
                if isinstance(row, dict) and isinstance(row.get('path'), str):
                    paths.add(row['path'])
        lm = (snap.get('bus') or {}).get('loaded_models')
        if isinstance(lm, dict):
            paths.update(v['path'] for v in lm.values() if isinstance(v, dict) and isinstance(v.get('path'), str))
        return paths

    def _configured_refs(self):
        """配置先落盘、加载后失败时，运行快照可能还是旧文件；不能遗漏重启引用。"""
        path = self._local('/etc/robot/feetech-ft5/robotd.toml')
        try:
            if path.stat().st_size > 2 << 20:
                raise ValueError('配置超过大小上限')
            with path.open('rb') as f:
                data = tomllib.load(f)
            policy = data.get('policy')
            if not isinstance(policy, dict):
                raise ValueError('缺少 policy 配置')
            rows = policy.get('skill', [])
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise ValueError('policy.skill 格式无效')
            paths = [policy.get(s) for s in SLOTS] + [r.get('path') for r in rows]
            if any(p is not None and (not isinstance(p, str) or p != 'none' and not p.startswith('/')) for p in paths):
                raise ValueError('模型配置路径无效')
            return {p for p in paths if p and p != 'none'}, None
        except (OSError, ValueError, RecursionError) as exc:
            return set(), f'无法读取主程序持久化配置，不能确认模型可删除：{exc}'

    def _normalize_refs(self, refs):
        try:
            return {self._remote(self._local(path).resolve()) for path in refs}
        except (OSError, RuntimeError) as exc:
            raise ModelError(f'模型引用路径无法解析，不能确认可删除：{exc}') from exc

    @staticmethod
    def _delete_reason(item, snap, refs, config_error):
        if item['source'] == 'builtin':
            return '内置默认模型不能删除'
        if snap['error'] or config_error:
            return snap['error'] or config_error
        policies = snap.get('policies') or {}
        if policies.get('change_error') or any(r.get('error') for r in Models._rows(policies, 'slots')):
            return '主程序模型加载有错误，无法确认旧配置引用，请先处理加载问题'
        if item['path'] in refs:
            return '模型仍被运行槽、技能或持久化配置引用，请先切换或恢复默认'
        return None

    async def view(self):
        snap = await self._snapshot()
        try:
            catalog, entries, library_error = await asyncio.to_thread(self._read_library, snap)
        except (OSError, ValueError) as exc:
            catalog, entries, library_error = {'defaults': {}}, [], f'模型库无法读取：{exc}'
        reason = library_error or self._idle_reason(snap)
        by_path = {i['path']: i['id'] for i in entries}
        slot_rows = {r.get('slot'): r for r in self._rows(snap.get('policies'), 'slots') if isinstance(r, dict)}
        refs = self._references(snap)
        configured, config_error = await asyncio.to_thread(self._configured_refs)
        refs.update(configured)
        try:
            refs = await asyncio.to_thread(self._normalize_refs, refs)
        except ModelError as exc:
            config_error = str(exc)
        slots = []
        for slot in SLOTS:
            row = slot_rows.get(slot, {})
            default = catalog['defaults'].get(slot, {})
            slots.append({'slot': slot, 'label': LABELS[slot], 'model_id': by_path.get(row.get('path')),
                          'path': row.get('path'), 'error': row.get('error'), 'default_id': default.get('id'),
                          'default_disabled': default.get('disabled') is True,
                          'is_default': bool(default and row and row.get('path') == default.get('path') and not row.get('error'))})
        slots_by_name = {s['slot']: s for s in slots}
        bindings = []
        for row in self._rows(snap.get('bindings'), 'bindings'):
            if not isinstance(row, dict) or row.get('button') not in BUTTONS:
                continue
            b, skill = row['button'], row.get('skill')
            slot = next((s for s, sk in SKILLS.items() if sk == skill), None)
            bindings.append({'button': b, 'label': BUTTONS[b], 'slot': slot, 'skill': skill,
                             'model_id': slots_by_name.get(slot, {}).get('model_id'), 'default_skill': DEFAULT_KEYS[b],
                             'overridden': row.get('overridden'), 'error': row.get('error')})
        items = []
        active = (snap.get('bus') or {}).get('active_model')
        for entry in entries:
            path, slot = entry['path'], entry['slot']
            chosen = [s['slot'] for s in slots if s.get('path') == path]
            delete_reason = self._delete_reason(entry, snap, refs, config_error)
            file_reason = None if entry['installed'] else '模型 policy.onnx 文件缺失或不安全，请重新导入'
            items.append({'id': entry['id'], 'name': entry['name'], 'source': entry['source'], 'task': entry.get('task'),
                          'suggested_slot': slot, 'allowed_slots': [slot], 'slots': chosen,
                          'running': bool(isinstance(active, dict) and active.get('path') == path),
                          'buttons': [b['button'] for b in bindings if b.get('model_id') == entry['id']],
                          'can_delete': delete_reason is None, 'reason': file_reason or delete_reason,
                          'sha12': (entry.get('sha256') or '')[:12] or None, 'path': path, 'installed': entry['installed']})
        available = await asyncio.to_thread(self._validator_available)
        reason = reason or (None if available else '本机没有可信模型运行器，无法预检模型')
        return {'items': items, 'slots': slots, 'bindings': bindings,
                'buttons': [{'button': b, 'label': l} for b, l in BUTTONS.items()],
                'capabilities': {'import': available and not library_error, 'select': reason is None,
                                 'rename': not library_error, 'delete': not snap['error'] and not config_error and not library_error,
                                 'reason': reason, 'import_reason': library_error or (None if available else '本机没有可信模型运行器，无法预检模型')},
                'error': library_error or snap['error']}

    async def _library(self, snap):
        catalog, entries, error = await self._disk(self._read_library, snap)
        if error:
            raise ModelError(error, status=409, code='library_corrupt')
        return catalog, entries

    @staticmethod
    async def _disk(function, *args):
        try:
            return await asyncio.to_thread(function, *args)
        except ModelError:
            raise
        except (OSError, ValueError) as exc:
            raise ModelError(f'模型文件读取或保存失败：{exc}', status=503) from exc

    @staticmethod
    def _item(entries, ident):
        item = next((i for i in entries if i['id'] == ident), None)
        if item is None:
            raise ModelError('找不到这个模型，请刷新列表')
        return item

    def _rename(self, item, catalog, name):
        with self._disk_lock:
            if item['source'] == 'builtin':
                catalog['defaults'][item['slot']]['name'] = name
                _atomic_json(self.catalog, catalog)
            else:
                path = self.directory / item['id'] / META
                record = _json(path, 64 << 10)
                record['name'] = name
                _atomic_json(path, record)

    def _delete(self, item):
        if not re.fullmatch(r'[a-f0-9]{32}', item['id']):
            raise ModelError('模型目录身份无效')
        target = self.directory / item['id']
        if target.is_symlink() or target.resolve().parent != self.directory.resolve():
            raise ModelError('模型目录路径不安全')
        shutil.rmtree(target)

    def _check_compatibility(self, item, catalog, snap):
        baseline = (catalog['defaults'].get('walk') or {}).get('signature')
        if not isinstance(baseline, dict):
            raise ModelError('缺少开机行走模型的 HOME / 舵机 P 合同，无法确认安全热切换')
        target = _signature(_json(self._local(item['path']).with_name('deployment-contract.json')))
        current_path = next((r.get('path') for r in self._rows(snap['policies'], 'slots')
                             if isinstance(r, dict) and r.get('slot') == 'walk'), None)
        if not current_path:
            raise ModelError('无法确认当前行走模型的控制合同')
        current = _signature(_json(self._local(current_path).with_name('deployment-contract.json')))
        for candidate in (current, target):
            if candidate.get('joints') != baseline.get('joints'):
                raise ModelError('模型关节顺序与开机控制合同不一致，不能热切换')
            if candidate.get('kp_fw') != baseline.get('kp_fw'):
                raise ModelError('模型舵机 P 与开机控制合同不同；需重新部署并重启主程序')
            a, b = candidate.get('home'), baseline.get('home')
            if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b) or any(abs(x - y) > 1e-8 for x, y in zip(a, b)):
                raise ModelError('模型 HOME 站姿与开机控制合同不同；需重新部署并重启主程序')
        if item['source'] == 'imported':
            digest = hashlib.sha256(self._local(item['path']).read_bytes()).hexdigest()
            if digest != item['sha256']:
                raise ModelError('导入模型文件已改变，SHA256 校验不通过，请重新导入')
        self._preflight(self._local(item['path']))

    def _check_selection_files(self, item, catalog, snap):
        try:
            self._check_compatibility(item, catalog, snap)
        except ModelError:
            raise
        except (OSError, ValueError, OverflowError) as exc:
            raise ModelError(f'无法读取或预检模型文件及部署合同：{exc}') from exc

    async def _write_rpc(self, method, params):
        result = await self._call(method, params, timeout=8.0)
        if not isinstance(result, dict) or result.get('accepted') is not True:
            raise ModelError('主程序拒绝配置修改：' + str((result or {}).get('reason', '未确认接受') if isinstance(result, dict) else '未返回结果'), status=409)

    async def _wait_loaded(self, slot, path, expected_skill=None):
        deadline = time.monotonic() + READBACK_TIMEOUT
        last = '尚未回读到模型已加载'
        while True:
            snap = await self._snapshot()
            rows = {r.get('slot'): r for r in self._rows(snap.get('policies'), 'slots') if isinstance(r, dict)}
            row = rows.get(slot, {})
            lm = (snap.get('bus') or {}).get('loaded_models')
            lm = lm if isinstance(lm, dict) else {}
            actual = lm.get(slot)
            expected = None if path == 'none' else path
            error = snap['error'] or (snap.get('policies') or {}).get('change_error') or row.get('error')
            skill_ok = True
            if expected_skill:
                sk, skpath = expected_skill
                cfg = next((r for r in self._rows(snap.get('skills'), 'skills') if isinstance(r, dict) and r.get('name') == sk), {})
                loaded = lm.get(sk)
                skill_ok = cfg.get('path') == skpath and isinstance(loaded, dict) and loaded.get('path') == skpath
            loaded_ok = actual is None if expected is None else isinstance(actual, dict) and actual.get('path') == expected
            if not error and row and row.get('path') == expected and isinstance(row.get('overridden'), bool) and loaded_ok and skill_ok:
                return
            last = str(error or ('槽路径或实际加载模型尚不一致' if not loaded_ok else '技能模型或槽回读尚不一致'))
            if time.monotonic() >= deadline:
                raise ModelError('模型加载未确认：' + last, status=503, code='load_unconfirmed')
            await asyncio.sleep(READBACK_POLL)

    async def _select(self, item, slot, catalog, snap):
        if slot not in SLOTS or item['slot'] != slot:
            raise ModelError('这个模型的用途与所选运行槽不匹配')
        if not item.get('installed'):
            raise ModelError('模型 policy.onnx 文件缺失或不安全，请重新导入')
        await asyncio.to_thread(self._check_selection_files, item, catalog, snap)
        # 预检可能持续数秒；写配置前重新确认卸力，不能沿用预检前的状态。
        snap = await self._require_idle()
        old = next((r.get('path') for r in self._rows(snap['policies'], 'slots')
                    if isinstance(r, dict) and r.get('slot') == slot), None)
        if old is None and slot == 'walk':
            raise ModelError('当前行走模型路径未知，无法保存回退来源')
        canonical = SKILLS.get(slot)
        if canonical and canonical not in snap['policies'].get('skills', []) and canonical not in snap['skills'].get('built_in', []):
            raise ModelError('主程序没有提供这个动作技能，不能只改模型槽后宣称选用成功')
        skill = next((r for r in self._rows(snap['skills'], 'skills')
                      if isinstance(r, dict) and r.get('name') == canonical and isinstance(r.get('path'), str)), None)
        changed_skill = skill is not None and slot not in ('sitstand', 'ground_pick') and skill['path'] != item['path']
        expected_skill = (canonical, item['path']) if changed_skill else None
        try:
            await self._write_rpc('robot.loadPolicy', {'slot': slot, 'path': item['path']})
            if changed_skill:
                await self._require_idle()
                await self._write_rpc('robot.setSkill', {'name': canonical, 'path': item['path']})
            await self._wait_loaded(slot, item['path'], expected_skill)
        except Exception as exc:
            try:
                await self._require_idle()
                await self._write_rpc('robot.loadPolicy', {'slot': slot, 'path': old or 'none'})
                if changed_skill:
                    await self._write_rpc('robot.setSkill', {'name': canonical, 'path': skill['path']})
                await self._wait_loaded(slot, old or 'none', (canonical, skill['path']) if changed_skill else None)
            except Exception as rollback:
                raise ModelError(f'模型切换失败：{exc}；旧配置回退也未确认：{rollback}', status=503, code='rollback_failed') from exc
            raise ModelError(f'模型切换失败：{exc}；已回退到原来的模型', status=503, code='load_failed') from exc

    async def _restore_disabled(self, slot, snap):
        old = next((r.get('path') for r in self._rows(snap['policies'], 'slots') if r['slot'] == slot), None)
        try:
            await self._require_idle()
            await self._write_rpc('robot.loadPolicy', {'slot': slot, 'path': 'none'})
            await self._wait_loaded(slot, 'none')
        except Exception as exc:
            try:
                await self._require_idle()
                await self._write_rpc('robot.loadPolicy', {'slot': slot, 'path': old or 'none'})
                await self._wait_loaded(slot, old or 'none')
            except Exception as rollback:
                raise ModelError(f'恢复默认未安装失败：{exc}；回退也未确认：{rollback}', status=503, code='rollback_failed') from exc
            raise ModelError(f'恢复默认未安装失败：{exc}；已回退原配置', status=503, code='load_failed') from exc

    async def _wait_bindings(self, expected):
        deadline = time.monotonic() + READBACK_TIMEOUT
        while True:
            try:
                data = await self._call('pad.bindings')
                rows = {r.get('button'): r for r in self._rows(data, 'bindings') if isinstance(r, dict)}
                if all(b in rows and rows[b].get('skill') == spec['skill'] and
                       rows[b].get('overridden') is spec['overridden'] and not rows[b].get('error')
                       for b, spec in expected.items()):
                    return
            except Exception:
                pass
            if time.monotonic() >= deadline:
                raise ModelError('按键配置写入后未能回读确认', status=503)
            await asyncio.sleep(READBACK_POLL)

    async def _bind(self, args, snap):
        op = args['op']
        rows = {r.get('button'): r for r in self._rows(snap['bindings'], 'bindings') if isinstance(r, dict)}
        changes = []
        if op == 'reset_bindings':
            changes = [(b, None) for b in BUTTONS]
        else:
            button = args.get('button')
            if not isinstance(button, str) or button not in BUTTONS:
                raise ModelError('只支持 A、X、LB、RB 和十字下按键')
            skill = ''
            if op == 'bind':
                slot = args.get('slot')
                if not isinstance(slot, str) or slot not in SKILLS:
                    raise ModelError('行走和独立站立不是可绑定的单次技能')
                selected = next((r for r in self._rows(snap['policies'], 'slots')
                                 if isinstance(r, dict) and r.get('slot') == slot), {})
                if not selected.get('path') or selected.get('error'):
                    raise ModelError('这个动作还没有可用的模型，请先选用模型')
                skill = SKILLS[slot]
                current = rows.get(button, {}).get('skill')
                if current and current != skill and args.get('replace') is not True:
                    label = next((LABELS[s] for s, sk in SKILLS.items() if sk == current), current)
                    raise ModelError(f'{BUTTONS[button]} 当前用于「{label}」，请确认替换后再修改', status=409, code='binding_conflict')
            changes = [(button, skill)]
        if any(b not in rows or not isinstance(rows[b].get('skill'), str) or not isinstance(rows[b].get('overridden'), bool) for b, _ in changes):
            raise ModelError('按键原配置回读不完整，无法保存回退来源', status=503)
        attempted = []
        try:
            expected = {}
            for button, skill in changes:
                await self._require_idle()
                attempted.append(button)
                params = {'button': button}
                if skill is not None:
                    params['skill'] = skill
                await self._write_rpc('pad.bind', params)
                target_skill = DEFAULT_KEYS[button] if skill is None else skill
                expected[button] = {'skill': target_skill, 'overridden': target_skill != DEFAULT_KEYS[button]}
            await self._wait_bindings(expected)
        except Exception as exc:
            try:
                for button in reversed(attempted):
                    await self._require_idle()
                    params = {'button': button}
                    if rows[button]['overridden']:
                        params['skill'] = rows[button]['skill']
                    await self._write_rpc('pad.bind', params)
                await self._wait_bindings({b: rows[b] for b in attempted})
            except Exception as rollback:
                raise ModelError(f'按键修改失败：{exc}；原按键配置回退也未确认：{rollback}', status=503, code='rollback_failed') from exc
            raise ModelError(f'按键修改失败：{exc}；已恢复原按键配置', status=503, code='binding_failed') from exc

    async def handle(self, args, by=None):
        if not isinstance(args, dict) or args.get('op') not in ('rename', 'select', 'restore', 'delete', 'bind', 'unbind', 'reset_bindings'):
            raise ModelError('不支持的模型管理操作')
        op = args['op']
        async with self._lock:
            snap = await self._snapshot()
            catalog, entries = await self._library(snap)
            if op == 'rename':
                item = self._item(entries, args.get('id'))
                await self._disk(self._rename, item, catalog, self._name(args.get('name')))
            elif op == 'delete':
                item = self._item(entries, args.get('id'))
                refs, config_error = await asyncio.to_thread(self._configured_refs)
                refs.update(self._references(snap))
                refs = await asyncio.to_thread(self._normalize_refs, refs)
                reason = self._delete_reason(item, snap, refs, config_error)
                if reason:
                    raise ModelError(reason, status=409, code='model_in_use')
                await self._disk(self._delete, item)
            else:
                reason = self._idle_reason(snap)
                if reason:
                    raise ModelError(reason, status=409, code='not_idle')
                if op in ('select', 'restore'):
                    slot = args.get('slot')
                    if slot not in SLOTS:
                        raise ModelError('无法识别这个模型运行槽')
                    ident = args.get('id') if op == 'select' else (catalog['defaults'].get(slot) or {}).get('id')
                    if not ident:
                        raise ModelError('没有保存这个动作的内置默认模型，无法恢复')
                    if op == 'restore' and catalog['defaults'][slot].get('disabled'):
                        await self._restore_disabled(slot, snap)
                    else:
                        item = self._item(entries, ident)
                        await self._select(item, slot, catalog, snap)
                else:
                    await self._bind(args, snap)
        if self.log:
            self.log.write('模型管理配置已确认', cat='models', raw=f'op={op}; by={by or "web"}')
        return await self.view()
