import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

const moduleUrl = new URL('../web/models.js', import.meta.url);
const ui = existsSync(moduleUrl) ? await import(moduleUrl) : {};
const view = {
  slots: [
    { slot: 'walk', label: '走路 / 保持', model_id: 'walk-default' },
    { slot: 'ground_pick', label: '低头捡东西', model_id: 'duck-new' },
    { slot: 'roulade', label: '前滚翻', model_id: 'flip-default' },
  ],
  items: [{ id: 'duck-new', name: '我的捡东西' }, { id: 'flip-default', name: '内置前滚翻' }],
  bindings: [{ button: 'a', label: 'A', slot: 'roulade', skill: 'roulade', model_id: 'flip-default' }],
};

test('using an installed model never presents it as currently executing', () => {
  assert.equal(typeof ui.modelState, 'function', 'model status helper must exist');
  assert.deepEqual(ui.modelState({ installed: true, slots: ['ground_pick'], running: false }), ['已安装', '当前选用']);
  assert.deepEqual(ui.modelState({ installed: true, slots: [], running: true }), ['已安装', '正在执行']);
});

test('a model can only be offered in server-approved purposes', () => {
  assert.equal(typeof ui.modelSlots, 'function', 'compatible purpose helper must exist');
  assert.deepEqual(ui.modelSlots(view, { allowed_slots: ['ground_pick', 'unknown'], suggested_slot: 'walk' }).map(s => s.slot), ['ground_pick']);
  assert.deepEqual(ui.modelSlots(view, { task: 'unknown' }), []);
});

test('a bound key requires an explicit replacement decision that names the old action', () => {
  assert.equal(typeof ui.bindingChoice, 'function', 'binding decision helper must exist');
  const choice = ui.bindingChoice(view, 'a', 'ground_pick');
  assert.equal(choice.valid, true);
  assert.equal(choice.conflict, true);
  assert.equal(choice.currentLabel, '前滚翻 · 内置前滚翻');
  assert.equal(choice.targetLabel, '低头捡东西 · 我的捡东西');
  assert.equal(ui.bindingChoice(view, 'a', 'roulade').already, true);
});

test('walking cannot be assigned to a skill button and reserved buttons cannot be changed', () => {
  assert.equal(typeof ui.bindingChoice, 'function', 'binding decision helper must exist');
  assert.equal(ui.bindingChoice(view, 'a', 'walk').valid, false);
  assert.equal(ui.bindingChoice(view, 'start', 'ground_pick').valid, false);
});

test('missing runtime capabilities disable changes and retain a useful reason', () => {
  assert.equal(typeof ui.operationAvailability, 'function', 'operation capability helper must exist');
  const unavailable = ui.operationAvailability({ error: '主程序没连接' });
  assert.equal(unavailable.select, false);
  assert.equal(unavailable.import, false);
  assert.equal(unavailable.reason, '主程序没连接');
  assert.equal(ui.operationAvailability({ capabilities: { rename: true, select: false, reason: '请先卸力' } }).rename, true);
});

test('physical key capture only accepts a fresh supported rising edge after a baseline', () => {
  assert.equal(typeof ui.captureKey, 'function', 'physical key capture helper must exist');
  const frame = buttons => ({ state: 'attached', buttons, last_ms: 0 });
  assert.equal(ui.captureKey(null, frame({ south: true })), null);
  assert.equal(ui.captureKey({ south: true }, frame({ south: true })), null);
  assert.equal(ui.captureKey({}, frame({ start: true })), null);
  assert.equal(ui.captureKey({}, frame({ south: true })), 'a');
  assert.equal(ui.captureKey({}, frame({ dd: true })), 'dpad_down');
  assert.equal(ui.captureKey({}, { ...frame({ west: true }), state: 'off' }), null);
  assert.equal(ui.captureKey({}, { ...frame({ west: true }), last_ms: 4000 }), null);
  assert.equal(ui.captureKey({}, frame({ south: true, west: true })), null);
});

test('physical capture falls back to the selector when a key can directly start recovery', () => {
  assert.equal(typeof ui.captureAllowed, 'function');
  const bindings = ['a', 'x', 'lb', 'rb', 'dpad_down'].map(button => ({ button, skill: '' }));
  assert.equal(ui.captureAllowed({ bindings }), true);
  bindings[2].skill = 'stand_test'; // robot.do has an explicit enable-independent recovery path.
  assert.equal(ui.captureAllowed({ bindings }), false);
  assert.equal(ui.captureAllowed({ bindings: [] }), false);
});

test('a refresh started before a model change cannot replace the confirmed result', async () => {
  assert.equal(typeof ui.createModelReader, 'function', 'model refresh ordering helper must exist');
  let finish;
  const reader = ui.createModelReader(() => new Promise(resolve => { finish = resolve; }));
  const refresh = reader.read();
  reader.invalidate(); // A rename/select/upload has started after this request.
  finish({ ok: true, data: { items: [{ name: '旧名称' }] } });
  assert.equal(await refresh, null);
});

test('a stale failed refresh cannot turn a successful model operation into an offline page', async () => {
  assert.equal(typeof ui.createModelReader, 'function', 'model refresh ordering helper must exist');
  let fail;
  const reader = ui.createModelReader(() => new Promise((resolve, reject) => { fail = reject; }));
  const refresh = reader.read();
  reader.invalidate();
  fail(new Error('旧请求超时'));
  assert.equal(await refresh, null);
  const current = ui.createModelReader(async () => { throw new Error('当前请求超时'); });
  await assert.rejects(current.read(), /当前请求超时/);
});

// ── 更多动作 ──
const actionView = {
  ...view,
  actions: [{ id: 'a1', name: '深蹲', skill: 'crouch', state: 'loaded', buttons: [] },
    { id: 'a2', name: '鞠躬', skill: null, state: 'none', suggest: { name: 'bow', duration: 2 } }],
  manual_actions: [{ name: 'idle_chest', skill: 'idle_chest', state: 'loaded' }],
  bindings: [{ button: 'a', label: 'A', slot: 'roulade', skill: 'roulade', model_id: 'flip-default' },
    { button: 'rb', label: 'RB', slot: null, skill: 'crouch', action_name: '深蹲' }],
};

test('custom actions bind by name and only once they are loaded', () => {
  const choice = ui.bindingChoice(actionView, 'a', 'skill:crouch');
  assert.equal(choice.valid, true);
  assert.equal(choice.conflict, true);
  assert.equal(choice.targetLabel, '深蹲');
  assert.deepEqual(choice.args, { op: 'bind', button: 'a', skill: 'crouch' });
  assert.equal(ui.bindingChoice(actionView, 'rb', 'skill:crouch').already, true);
  assert.equal(ui.bindingChoice(actionView, 'lb', 'skill:idle_chest').valid, true);
  assert.equal(ui.bindingChoice(actionView, 'lb', 'skill:bow').valid, false);
  assert.equal(ui.bindingChoice(actionView, 'start', 'skill:crouch').valid, false);
  assert.equal(ui.bindingChoice(actionView, 'a', 'ground_pick').currentLabel, '前滚翻 · 内置前滚翻');
  assert.equal(ui.bindingChoice(actionView, 'rb', 'ground_pick').currentLabel, '深蹲');
});

test('action form defaults to scale 1 and parses three numbers', () => {
  const form = ui.actionForm(actionView.actions[1]);
  assert.equal(form.name, 'bow');
  assert.equal(form.action_scale, '1');
  const ok = ui.actionArgs({ ...form, command: '1，0 0' });
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.args.command, [1, 0, 0]);
  assert.equal(ok.args.action_scale, 1);
  assert.equal(ok.args.duration, 2);
  for (const bad of [{ name: 'Bow' }, { duration: '0' }, { command: '1, 0' }, { unwind: '5, 0, 0' }, { action_scale: '0' }, { gain_ratio: '3' }]) {
    assert.equal(ui.actionArgs({ ...form, ...bad }).ok, false, JSON.stringify(bad));
  }
  assert.equal(ui.actionState({ state: 'failed' }), '没加载成功');
});

test('physical capture stays off when a key runs a custom action', () => {
  const rows = ['a', 'x', 'lb', 'rb', 'dpad_down'].map(button => ({ button, skill: button === 'rb' ? 'crouch' : '' }));
  assert.equal(ui.captureAllowed({ bindings: rows }), false);
});

// ── 动作页 ──
const actionsUrl = new URL('../web/actions.js', import.meta.url);
const ac = existsSync(actionsUrl) ? await import(actionsUrl) : {};
const pageView = {
  slots: ['walk', 'sitstand', 'ground_pick', 'kick_left', 'kick_right', 'roulade', 'stand'].map((slot) => ({ slot, label: slot, model_id: 'builtin-' + slot, path: `/m/${slot}/policy.onnx` })),
  items: [], skill_durations: { roulade: 1, kick_left: 0.5 }, loaded_skills: ['roulade', 'kick_left', 'kick_right', 'crouch'],
  actions: [{ id: 'c1', name: '深蹲', skill: 'crouch', state: 'loaded', buttons: ['rb'], params: { duration: 3 } },
    { id: 'i1', name: '抖一抖', skill: null, state: 'none', suggest: { name: 'idle_chest', duration: 3.1 }, buttons: [] }],
  manual_actions: [],
  bindings: [{ button: 'a', skill: 'ground_pick', slot: 'ground_pick' }, { button: 'x', skill: 'roulade', slot: 'roulade' },
    { button: 'lb', skill: 'kick_left', slot: 'kick_left' }, { button: 'rb', skill: 'crouch', slot: null, action_name: '深蹲' },
    { button: 'dpad_down', skill: 'sit_toggle', slot: 'sitstand' }],
};

test('the actions page lists factory moves, walking and imported actions together', () => {
  const list = ac.actionList(pageView);
  assert.deepEqual(list.map((x) => x.name), ['前滚翻', '踢左脚', '踢右脚', '捡东西', '坐下 / 站起', '走路', '深蹲', '抖一抖']);
  const roll = list.find((x) => x.skill === 'roulade');
  assert.equal(roll.duration, '约 1 秒');
  assert.deepEqual(roll.keys, ['x']);
  assert.match(roll.image, /stills\/roulade\.webp$/);
  const idle = list.find((x) => x.name === '抖一抖');
  assert.equal(idle.ready, false);
  assert.match(idle.image, /stills\/idle_chest\.webp$/);  // 11-27 作者同意随 Studio 发布
  assert.equal(list.find((x) => x.name === '深蹲').duration, '3 秒');
});

test('key caps show action names and binding asks before replacing', () => {
  assert.equal(ac.keyName(pageView, 'rb'), '深蹲');
  assert.equal(ac.keyName(pageView, 'dpad_down'), '坐下 / 站起');
  const list = ac.actionList(pageView);
  const crouch = list.find((x) => x.skill === 'crouch');
  const c = ac.keyChoice(pageView, crouch, 'a');
  assert.equal(c.valid, true);
  assert.ok(c.confirm.some((l) => l.includes('捡东西')));
  assert.deepEqual(c.args, { op: 'bind', button: 'a', skill: 'crouch', replace: true });
  const x = ac.keyChoice(pageView, crouch, 'x');
  assert.ok(x.confirm.some((l) => l.includes('连着发')));
  const kick = list.find((x) => x.skill === 'kick_right');
  assert.deepEqual(ac.keyChoice(pageView, kick, 'lb').args, { op: 'bind', button: 'lb', slot: 'kick_right', replace: true });
  assert.equal(ac.keyChoice(pageView, list.find((x) => x.name === '抖一抖'), 'a').valid, false);
  assert.equal(ac.keyChoice(pageView, list.find((x) => x.kind === 'walk'), 'a').valid, false);
});
