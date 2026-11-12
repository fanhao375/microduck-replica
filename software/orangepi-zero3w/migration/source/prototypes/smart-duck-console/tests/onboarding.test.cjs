'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const SESSION_KEY = 'smart-duck-first-start-v1';
const FINE_STEPS = ['still', 'fwd', 'back', 'left', 'right'];
const SOURCES = ['common.js', 'devices.js', 'imu.js', 'system.js', 'onboarding.js']
  .map(name => [name, fs.readFileSync(path.join(ROOT, name), 'utf8')]);

// Load production state readers and the real workflow; only browser effects are stubbed.
function fixture(storage = new Map()) {
  const calls = { render: 0, stop: 0, release: 0, commit: 0, unload: 0, routes: [], toasts: [] };
  const document = { addEventListener() {}, querySelector: () => null, querySelectorAll: () => [] };
  const sessionStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  };
  const context = vm.createContext({
    window: { addEventListener() {} }, document, sessionStorage, location: { hash: '#/overview' },
    console, performance: { now: () => 0 }, setTimeout: () => 0, clearTimeout() {},
    requestAnimationFrame: () => 0,
  });
  let P;
  for (const [name, source] of SOURCES) {
    if (name === 'onboarding.js') {
      P = context.window.P;
      P.imuPage.getViewerState(); // Initialize the real IMU state without rendering WebGL.
      P.state.remote = { paired: false, netpad: false, mode: '走路', posture: '已卸力', axes: { lx: 0, ly: 0, rx: 0, ry: 0 }, estopResult: '' };
      Object.assign(P, {
        render: () => calls.render++, toast: message => calls.toasts.push(message),
        navigate: route => calls.routes.push(route), openModal() {},
        openCalibrationCommit: () => calls.commit++, releaseBus: () => calls.release++,
        stopAllOutputs() {
          calls.stop++;
          Object.assign(P.state, { torque: false, standing: false, jointTorque: {} });
          P.state.remote.axes = { lx: 0, ly: 0, rx: 0, ry: 0 };
          P.state.remote.posture = '已卸力';
          P.stopDevice();
        },
        emergency() { calls.unload++; P.stopAllOutputs(); P.onboarding.record('unload'); },
      });
    }
    vm.runInContext(source, context, { filename: name });
  }
  return { P, O: P.onboarding, calls, storage };
}

function startAndCheck(env) {
  env.O.handle('onboarding-start');
  env.O.handle('onboarding-check');
  assert.equal(env.P.state.onboarding.checked, true);
}
function reportZeroes(env, count = 15) {
  const { P } = env;
  P.state.devices.calibrated = Object.fromEntries(P.devices.jointIds.slice(0, count)
    .map(id => [id, { zero: 2048, direction: P.state.devices.directions[id] ?? 1 }]));
  P.state.calibrationPending.joints = [...P.devices.jointIds.slice(0, count)];
  env.O.record('calibration-changed');
}
function reportFineResult(env) {
  const i = env.P.state.devices.imu;
  i.mount = { verified: true, source: 'coarse-demo' };
  Object.assign(i.fine, { phase: 'done', done: Object.fromEntries(FINE_STEPS.map(key => [key, true])) });
  i.result = { demo: true, complete: true };
  env.P.state.calibrationPending.imu = true;
}
function readyForCommit(env) {
  startAndCheck(env);
  reportZeroes(env);
  reportFineResult(env);
  env.O.sync();
  assert.equal(env.O.current().id, 'commit');
}
function reportCommitted(env) {
  // This is the main program's successful readback fixture, not a second workflow implementation.
  Object.assign(env.P.state, { calibrationStatus: 'complete', boot: 'ready', calibrationPending: { joints: [], imu: false } });
  env.O.record('committed');
}
function readyForStop(env) {
  readyForCommit(env);
  reportCommitted(env);
  assert.equal(env.O.current().id, 'stop-web');
}
function reportStanding(env) {
  Object.assign(env.P.state, { torque: true, standing: true, jointTorque: {} });
  env.P.state.remote.posture = '站立';
  env.O.record('stand');
}
function reportUnloaded(env, event = 'unload') {
  Object.assign(env.P.state, { torque: false, standing: false, jointTorque: {} });
  env.P.state.remote.posture = '已卸力';
  env.O.record(event);
}
function verifiedStop(env) {
  readyForStop(env);
  reportStanding(env);
  reportUnloaded(env);
  assert.equal(env.P.state.onboarding.stopVerified, true);
}

test('starting explicitly resets old progress and outputs', () => {
  const env = fixture(), { P, O } = env;
  assert.equal(O.current().id, 'welcome');
  P.state.torque = true;
  P.state.standing = true;
  P.state.remote.axes.ly = -1;
  O.handle('onboarding-start');
  assert.equal(env.calls.stop, 1);
  assert.equal(P.state.torque, false);
  assert.equal(P.state.standing, false);
  assert.equal(P.state.remote.axes.ly, 0);
  assert.equal(P.state.boot, 'commissioning');
  assert.equal(P.devices.calibrationProgress().done, 0);
  assert.equal(P.state.devices.imu.mount.verified, false);
  assert.equal(O.current().id, 'check');
});

test('self-check uses actual readback, not a user checkbox or old setup markers', () => {
  const env = fixture(), { P, O } = env;
  O.reset();
  P.state.setupSteps = ['done', 'done', 'done', 'done', 'done', 'done'];
  P.state.onboarding.checked = true;
  P.state.telemetry.servos.online = 14;
  O.sync();
  assert.equal(O.current().id, 'check');
  assert.notEqual(O.motionBlock(), '');
  O.handle('onboarding-check');
  assert.equal(P.state.onboarding.checked, false);
  P.state.telemetry.servos.online = 15;
  O.handle('onboarding-check');
  assert.equal(P.state.onboarding.checked, true);
  assert.equal(O.current().id, 'servos');
});

for (const [name, mutate] of [
  ['duplicate IDs', P => { P.state.telemetry.servos.ids[14] = P.state.telemetry.servos.ids[0]; }],
  ['unknown joint ID', P => { P.state.telemetry.servos.ids[14] = 99; }],
  ['missing IDs', P => { delete P.state.telemetry.servos.ids; }],
  ['stale IMU', P => { P.state.telemetry.imu.fresh = false; }],
  ['unknown voltage', P => { P.state.batteryVolts = NaN; }],
  ['voltage at the configured floor', P => { P.state.batteryVolts = P.state.system.battery.floor; }],
  ['program starting', P => { P.state.boot = 'starting'; }],
  ['owned servo bus', P => { P.state.owned = true; }],
]) test(`self-check refuses ${name}`, () => {
  const env = fixture();
  env.O.reset();
  mutate(env.P);
  env.O.handle('onboarding-check');
  assert.equal(env.P.state.onboarding.checked, false);
  assert.notEqual(env.P.state.onboarding.checkError, '');
});

test('all fifteen calibrated zeroes are required and direction changes invalidate a joint', () => {
  const env = fixture();
  startAndCheck(env);
  reportZeroes(env, 14);
  assert.equal(env.O.current().id, 'servos');
  assert.notEqual(env.O.commitBlock(), '');
  reportZeroes(env);
  assert.equal(env.O.current().id, 'imu-direction');
  env.P.state.devices.directions[10] = -1;
  env.O.sync();
  assert.equal(env.P.devices.calibrationProgress().done, 14);
  assert.equal(env.O.current().id, 'servos');
});

test('completed zeroes hand the held bus back before IMU work', () => {
  const env = fixture();
  startAndCheck(env);
  reportZeroes(env);
  env.P.state.owned = true;
  assert.equal(env.O.current().id, 'handoff');
  env.O.handle('onboarding-release');
  assert.equal(env.calls.release, 1);
  assert.equal(env.P.state.owned, true, 'requesting release is not confirmation');
  env.P.state.owned = false;
  env.O.sync();
  assert.equal(env.O.current().id, 'imu-direction');
});

test('unverified or active coarse IMU cannot be bypassed by a commit or ready flag', () => {
  const env = fixture();
  startAndCheck(env);
  reportZeroes(env);
  env.P.state.boot = 'ready';
  env.P.state.calibrationStatus = 'complete';
  env.P.state.calibrationPending = { joints: [], imu: false };
  env.O.record('committed');
  assert.equal(env.P.state.onboarding.committedSnapshot, null);
  assert.notEqual(env.O.motionBlock(), '');
  reportFineResult(env);
  env.P.state.devices.imu.coarse.active = true;
  assert.notEqual(env.O.commitBlock(), '');
  assert.notEqual(env.O.motionBlock(), '');
});

test('a trusted current mount may skip coarse work, but this guide requires the five fine results', () => {
  const env = fixture();
  startAndCheck(env);
  reportZeroes(env);
  env.P.state.devices.imu.mount = { verified: true, source: 'existing' };
  assert.equal(env.O.current().id, 'imu-fine');
  Object.assign(env.P.state.devices.imu.fine.done, Object.fromEntries(FINE_STEPS.map(key => [key, true])));
  assert.notEqual(env.O.commitBlock(), '', 'manual five checks without a result are not success');
  reportFineResult(env);
  env.P.state.devices.imu.fine.done.left = false;
  assert.notEqual(env.O.commitBlock(), '');
  env.P.state.devices.imu.fine.done.left = true;
  assert.equal(env.O.commitBlock(), '');
  assert.equal(env.O.current().id, 'commit');
});

test('failed or pending commit readback cannot authorize motion', () => {
  const env = fixture();
  readyForCommit(env);
  env.P.state.boot = 'ready';
  env.P.state.calibrationStatus = 'failed';
  env.O.record('committed');
  assert.equal(env.O.current().id, 'commit');
  assert.notEqual(env.O.motionBlock(), '');
  env.P.state.calibrationStatus = 'complete';
  env.O.sync();
  assert.equal(env.O.current().id, 'commit', 'pending changes remain uncommitted');
  reportCommitted(env);
  assert.equal(env.O.motionBlock('exp-stand'), '');
});

test('selected model requires matching loaded readback and allows retry', () => {
  const env = fixture();
  readyForCommit(env);
  reportCommitted(env);
  env.P.state.system.loaded.walk = null;
  env.O.sync();
  assert.equal(env.O.current().id, 'models');
  assert.notEqual(env.O.motionBlock(), '');
  env.P.state.system.loaded.walk = env.P.state.system.slots.walk;
  env.P.state.system.modelError = 'readback failed';
  assert.equal(env.O.current().id, 'models');
  env.P.state.system.modelError = '';
  env.O.sync();
  assert.equal(env.O.current().id, 'stop-web');
});

test('web control advances without a physical gamepad; switching control resets the stop evidence', () => {
  const env = fixture();
  readyForStop(env);
  assert.equal(env.P.state.remote.paired, false);
  assert.equal(env.O.motionBlock('exp-stand'), '');
  assert.notEqual(env.O.motionBlock('exp-walk'), '');
  env.O.handle('onboarding-control', { value: 'pad' });
  assert.equal(env.O.current().id, 'stop-pad');
  env.O.handle('onboarding-control', { value: 'web' });
  env.P.state.remote.paired = true;
  assert.equal(env.O.current().id, 'disconnect');
  env.P.state.remote.paired = false;
  reportStanding(env);
  reportUnloaded(env);
  assert.equal(env.O.current().id, 'walk');
  env.O.handle('onboarding-control', { value: 'pad' });
  assert.equal(env.P.state.onboarding.stopVerified, false);
});

test('stop verification requires standing followed by a confirmed full unload', () => {
  const env = fixture();
  readyForStop(env);
  reportUnloaded(env);
  assert.equal(env.P.state.onboarding.stopVerified, false);
  env.O.record('stand');
  assert.equal(env.P.state.onboarding.standSeen, false);
  Object.assign(env.P.state, { torque: false, standing: true, jointTorque: { 10: true } });
  env.O.record('stand');
  assert.equal(env.P.state.onboarding.standSeen, false, 'partial torque does not establish standing');
  reportStanding(env);
  assert.equal(env.O.current().id, 'unload');
  env.O.record('unload');
  assert.equal(env.P.state.onboarding.stopVerified, false, 'button event with torque still held is not unload readback');
  env.P.state.offline = true;
  reportUnloaded(env);
  assert.equal(env.P.state.onboarding.stopVerified, false);
  env.P.state.offline = false;
  reportStanding(env);
  reportUnloaded(env);
  assert.equal(env.P.state.onboarding.stopVerified, true);
});

test('only nonzero walking input completes the workflow, not policy enable or head motion', () => {
  const env = fixture();
  verifiedStop(env);
  reportStanding(env);
  env.P.state.remote.posture = '行走中';
  env.O.record('move');
  assert.equal(env.P.state.onboarding.complete, false);
  env.P.state.remote.axes.ly = -0.05;
  env.O.record('move');
  assert.equal(env.P.state.onboarding.complete, false);
  env.P.state.remote.axes.ly = -0.3;
  env.P.state.remote.mode = '头部';
  env.O.record('move');
  assert.equal(env.P.state.onboarding.complete, false);
  env.P.state.remote.mode = '走路';
  env.O.record('move');
  assert.equal(env.P.state.onboarding.moved, true);
  assert.equal(env.P.state.onboarding.complete, true);
  assert.equal(env.O.current().id, 'complete');
  assert.equal(env.O.active(), false);
});

for (const [name, mutate] of [
  ['offline', P => { P.state.offline = true; }],
  ['stale', P => { P.state.stale = true; }],
  ['bus owned', P => { P.state.owned = true; }],
  ['model readback missing', P => { P.state.system.loaded.walk = null; }],
]) test(`stop evidence and completion are cleared after ${name}`, () => {
  const env = fixture();
  verifiedStop(env);
  mutate(env.P);
  env.O.sync();
  assert.equal(env.P.state.onboarding.stopVerified, false);
  assert.equal(env.P.state.onboarding.complete, false);
});

for (const [name, mutate] of [
  ['zero changed', P => { P.state.devices.calibrated[10].zero = 2047; }],
  ['direction changed', P => { P.state.devices.directions[10] = -1; }],
  ['IMU result changed', P => { P.state.devices.imu.result = null; }],
  ['IMU type changed', P => { P.state.devices.imu.savedProfile = 'qinmo'; }],
]) test(`a committed calibration credential expires when ${name}`, () => {
  const env = fixture();
  verifiedStop(env);
  mutate(env.P);
  env.O.sync();
  assert.equal(env.P.state.onboarding.committedSnapshot, null);
  assert.equal(env.P.state.onboarding.stopVerified, false);
  assert.notEqual(env.O.motionBlock(), '');
});

test('explicit recalibration invalidates a credential even before replacement values exist', () => {
  const env = fixture();
  verifiedStop(env);
  env.O.record('calibration-changed');
  assert.equal(env.P.state.onboarding.committedSnapshot, null);
  assert.equal(env.P.state.onboarding.stopVerified, false);
  assert.equal(env.P.state.onboarding.moved, false);
});

test('changing a mechanical zero also expires the old standing correction', () => {
  const env = fixture();
  verifiedStop(env);
  env.O.record('calibration-changed', 'servo');
  assert.equal(env.P.state.devices.imu.result, null);
  assert.equal(env.P.state.calibrationPending.imu, false);
  assert.equal(env.O.current().id, 'imu-fine');
  assert.notEqual(env.O.commitBlock(), '');
});

test('session restore retains calibration progress but never restores standing or torque', () => {
  const env = fixture();
  readyForCommit(env);
  env.P.state.torque = true;
  env.P.state.standing = true;
  env.P.state.jointTorque = { 10: true };
  env.P.state.devices.imu.standing = true;
  env.P.state.onboarding.standSeen = true;
  env.O.save();
  const restored = fixture(env.storage);
  assert.equal(restored.P.devices.calibrationProgress().done, 15);
  assert.equal(restored.P.state.devices.imu.result.complete, true);
  assert.equal(restored.O.current().id, 'commit');
  assert.equal(restored.P.state.torque, false);
  assert.equal(restored.P.state.standing, false);
  assert.equal(restored.P.state.devices.imu.standing, false);
  assert.equal(restored.P.state.onboarding.standSeen, false);
  assert.equal(Object.keys(restored.P.state.jointTorque).length, 0);
  assert.equal(Object.values(restored.P.state.remote.axes).every(value => value === 0), true);
});

test('restoring an interrupted commit requires retry instead of treating it as complete', () => {
  const env = fixture();
  readyForCommit(env);
  env.P.state.calibrationStatus = 'verifying';
  env.O.save();
  const restored = fixture(env.storage);
  assert.equal(restored.P.state.calibrationStatus, 'failed');
  assert.equal(restored.O.current().id, 'commit');
  assert.notEqual(restored.O.motionBlock(), '');
});

test('unavailable session storage or malformed JSON does not prevent a fresh workflow', () => {
  const malformed = fixture(new Map([[SESSION_KEY, '{bad JSON']]));
  assert.equal(malformed.O.current().id, 'welcome');
  const denied = new Map();
  denied.get = denied.set = () => { throw new Error('storage unavailable'); };
  const env = fixture(denied);
  assert.doesNotThrow(() => env.O.reset());
  assert.equal(env.O.current().id, 'check');
});

test('unfinished guide exposes only one next action and delegates rather than assuming success', () => {
  const env = fixture();
  env.O.handle('onboarding-start');
  assert.equal(env.O.handle('not-an-onboarding-action'), false);
  env.O.handle('onboarding-check');
  env.O.handle('onboarding-continue');
  assert.equal(env.calls.routes.at(-1), 'devices/servos');
  assert.equal(env.O.current().id, 'servos');
  assert.equal(env.O.contextMarkup('overview'), '');
  assert.equal(env.O.contextMarkup('devices', 'servos').includes('data-action="onboarding-continue"'), false);
});

test('a partial stored IMU object cannot crash a restored guide', () => {
  const env = fixture();
  readyForCommit(env);
  env.O.save();
  const saved = JSON.parse(env.storage.get(SESSION_KEY));
  saved.devices.imu = {};
  env.storage.set(SESSION_KEY, JSON.stringify(saved));
  const restored = fixture(env.storage);
  assert.doesNotThrow(() => restored.O.current());
  assert.notEqual(restored.O.current().id, 'complete');
  assert.equal(restored.P.state.onboarding.committedSnapshot, null);
  assert.equal(restored.P.state.onboarding.stopVerified, false);
});

test('nonzero input cannot complete an unloaded or unconfirmed standing state', () => {
  const env = fixture();
  verifiedStop(env);
  env.P.state.remote.posture = '行走中';
  env.P.state.remote.axes.ly = -0.3;
  env.O.record('move');
  assert.equal(env.P.state.onboarding.complete, false, 'unloaded state cannot count as moving');
  Object.assign(env.P.state, { torque: true, standing: false });
  env.O.record('move');
  assert.equal(env.P.state.onboarding.complete, false, 'torque alone is not confirmed standing');
});

test('gamepad choice requires its own connected gamepad stop result', () => {
  const env = fixture();
  readyForStop(env);
  env.O.handle('onboarding-control', { value: 'pad' });
  env.P.state.remote.paired = true;
  reportStanding(env);
  reportUnloaded(env, 'unload');
  assert.equal(env.P.state.onboarding.stopVerified, false, 'global unload is not the selected gamepad test');
  env.P.state.remote.paired = false;
  reportStanding(env);
  reportUnloaded(env, 'pad-estop');
  assert.equal(env.P.state.onboarding.stopVerified, false, 'unconnected gamepad has no stop evidence');
  env.P.state.remote.paired = true;
  reportStanding(env);
  env.P.state.remote.estopResult = '流程演示完成';
  reportUnloaded(env, 'pad-estop');
  assert.equal(env.P.state.onboarding.stopVerified, true);
});


test('a completed session restores completion without replaying its last movement', () => {
  const env = fixture();
  verifiedStop(env);
  reportStanding(env);
  env.P.state.remote.posture = '行走中';
  env.P.state.remote.axes.ly = -0.3;
  env.O.record('move');
  assert.equal(env.P.state.onboarding.complete, true);
  const restored = fixture(env.storage);
  restored.O.sync();
  assert.equal(restored.O.current().id, 'complete');
  assert.equal(restored.P.state.torque, false);
  assert.equal(restored.P.state.standing, false);
  assert.equal(restored.P.state.devices.imu.standing, false);
  assert.equal(Object.values(restored.P.state.remote.axes).every(value => value === 0), true);
});
