import assert from 'node:assert/strict';
import { test } from 'node:test';
import { recognizePhoneRecording } from '../web/m/voice.js';

test('a cancelled recording never starts speech recognition', async () => {
  const turn = new AbortController();
  turn.abort();
  const recognized = await recognizePhoneRecording(new ArrayBuffer(0), turn.signal,
    () => assert.fail('cancelled audio was sent to the board'),
    () => assert.fail('cancelled audio dispatched a command'));
  assert.equal(recognized, false);
});

test('a late recognition result cannot dispatch after cancellation', async () => {
  const turn = new AbortController();
  let resolve;
  const result = recognizePhoneRecording(new ArrayBuffer(0), turn.signal,
    () => new Promise((done) => { resolve = done; }),
    () => assert.fail('stale transcription dispatched a command'));
  turn.abort();
  resolve('stand up');
  assert.equal(await result, false);
});

test('a current recognition result dispatches exactly once', async () => {
  const heard = [];
  const turn = new AbortController();
  const result = await recognizePhoneRecording(new ArrayBuffer(0), turn.signal,
    async () => 'stand up', async (text) => heard.push(text));
  assert.equal(result, true);
  assert.deepEqual(heard, ['stand up']);
});
