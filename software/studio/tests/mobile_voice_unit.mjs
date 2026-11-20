import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseUtterance, runAction } from '../web/m/actions.js';

test('mobile voice rejects negated, quoted, and incomplete commands', () => {
  for (const text of ['不要前滚翻', '别站起来', '鸭鸭不要点头', '他说鸭鸭坐下', '“前滚翻”',
    '[unk] 前滚翻', '前滚翻 [unk]', '前滚翻好不好', '它在做前滚翻', '点头鸭鸭摇头',
    '不要停', '别停下', '他说别走', '鸭鸭']) {
    assert.equal(parseUtterance(text).cmd, null, text);
  }
});

test('mobile voice keeps clear commands and intentional stops', () => {
  for (const [text, cmd] of [['鸭鸭，请站起来。', 'stand'], ['低头捡东西', 'ground_pick'],
    ['走一小步', 'step'], ['滚翻', 'roulade'], ['打招呼', 'greet'], ['点点头', 'nod'],
    ['走', 'walk'], ['停下', 'stop'], ['别走', 'stop'], ['不要走', 'stop'], ['不走了', 'stop']]) {
    assert.equal(parseUtterance(text).cmd, cmd, text);
  }
  assert.equal(parseUtterance('点头', { requireWake: true }).cmd, null);
  assert.equal(parseUtterance('鸭鸭点头', { requireWake: true }).cmd, 'nod');
});

function stepFixture(t, getPosture = () => 'policy', respond = () => undefined) {
  let now = 0;
  let seq = 10;
  t.mock.method(performance, 'now', () => now);
  t.mock.method(globalThis, 'setTimeout', (resolve, ms) => {
    now += ms;
    queueMicrotask(resolve);
  });
  const calls = [];
  const link = {
    nextPadSeq: () => ++seq,
    async cmd(name, args) {
      assert.equal(name, 'pad.op');
      calls.push(args);
      return respond(args) ?? { ok: true, data: { sent: args.op === 'sticks' ? [0.16, 0, 0] : true } };
    },
  };
  return { calls, step: () => runAction(link, 'step', { getPosture }), stop: () => runAction(link, 'stop') };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test('phone step sends no movement before driving is confirmed', async (t) => {
  const { calls, step } = stepFixture(t, () => 'holding');
  const result = await step();
  assert.equal(result.ok, false);
  assert.deepEqual(calls.map(({ op }) => op), ['walk']);
});

test('phone step reports dropped input and requests owner-only cleanup', async (t) => {
  const { calls, step } = stepFixture(t, undefined, ({ op }) => (
    op === 'sticks' ? { ok: true, data: { dropped: '方向盘在别的页面' } } : undefined));
  const result = await step();
  assert.equal(result.ok, false);
  assert.match(result.error, /方向盘/);
  assert.deepEqual(calls.map(({ op }) => op), ['walk', 'sticks', 'stop']);
  assert.equal(calls[2].owned_only, true);
  assert.ok(calls[2].seq > calls[1].seq);
});

test('phone step cleans up after a failed send and reports failed stops', async (t) => {
  let failSend = true;
  const { calls, step } = stepFixture(t, undefined, ({ op }) => {
    if (op === 'sticks' && failSend) throw new Error('连接断开');
    if (op === 'stop') return { ok: false, reason: '停下没发出去' };
  });
  const first = await step();
  assert.equal(first.ok, false);
  assert.match(first.error, /连接断开/);
  assert.deepEqual(calls.map(({ op }) => op), ['walk', 'sticks', 'stop']);
  const firstStopSeq = calls.at(-1).seq;
  failSend = false;
  calls.length = 0;
  const second = await step();
  assert.equal(second.ok, false);
  assert.match(second.error, /停下没发出去/);
  assert.ok(calls[1].seq > firstStopSeq);
  assert.equal(calls.at(-1).op, 'stop');
});

test('phone step succeeds after sent movement and accepted neutral cleanup', async (t) => {
  const { calls, step } = stepFixture(t);
  assert.equal((await step()).ok, true);
  assert.equal(calls[0].op, 'walk');
  assert.ok(calls.slice(1, -1).length > 1);
  assert.ok(calls.slice(1, -1).every(({ op, ly }) => op === 'sticks' && ly < 0));
  assert.deepEqual(calls.at(-1), { op: 'stop', seq: calls.at(-2).seq + 1, owned_only: true });
});

test('explicit stop cancels a step waiting for its movement acknowledgement', async (t) => {
  const inFlight = deferred();
  const seen = deferred();
  let held = false;
  const { calls, step, stop } = stepFixture(t, undefined, ({ op }) => {
    if (op !== 'sticks' || held) return undefined;
    held = true;
    seen.resolve();
    return inFlight.promise;
  });
  const moving = step();
  await seen.promise;
  const stopping = stop();
  inFlight.resolve({ ok: true, data: { sent: [0.16, 0, 0] } });
  assert.equal((await stopping).ok, true);
  assert.equal((await moving).ok, false);
  assert.deepEqual(calls.map(({ op }) => op), ['walk', 'sticks', 'walk_stop']);
});

test('replacement waits for the old step neutral before sending its own frames', async (t) => {
  const inFlight = deferred();
  const seen = deferred();
  let held = false;
  const { calls, step } = stepFixture(t, undefined, ({ op }) => {
    if (op !== 'sticks' || held) return undefined;
    held = true;
    seen.resolve();
    return inFlight.promise;
  });
  const old = step();
  await seen.promise;
  const newer = step();
  inFlight.resolve({ ok: true, data: { sent: [0.16, 0, 0] } });
  assert.equal((await old).ok, false);
  assert.equal((await newer).ok, true);
  assert.deepEqual(calls.slice(0, 5).map(({ op }) => op), ['walk', 'sticks', 'stop', 'walk', 'sticks']);
  assert.ok(calls[2].seq < calls[4].seq);
});

test('leaving the page during policy setup cancels before another motion request', async (t) => {
  let posture = 'sitting';
  const setup = deferred();
  let held = false;
  const { calls, step } = stepFixture(t, () => posture, ({ op }) => {
    if (op !== 'walk' || held) return undefined;
    held = true;
    return setup.promise;
  });
  const moving = step();
  posture = null;
  setup.resolve({ ok: false, reason: '现在坐着' });
  assert.equal((await moving).ok, false);
  assert.deepEqual(calls.map(({ op }) => op), ['walk']);
});
