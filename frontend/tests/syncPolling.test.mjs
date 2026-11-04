import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Compile with the project's existing TypeScript dependency so tests also run
// on Node versions without native TypeScript support.
const source = readFileSync(new URL('../src/syncPolling.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const { watchSync } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(request, extra = {}) {
  const timers = [];
  const updates = [], finished = [], errors = [];
  const cancel = watchSync({
    request,
    update: job => updates.push(job),
    finish: job => finished.push(job),
    fail: error => errors.push(error),
    schedule: (callback, delay) => {
      const timer = { callback, delay, cancelled: false };
      timers.push(timer);
      return () => { timer.cancelled = true; };
    },
    ...extra,
  });
  return { timers, updates, finished, errors, cancel,
    tick: async () => {
      const timer = timers.shift();
      if (timer && !timer.cancelled) timer.callback();
      await flush();
    },
  };
}

test('recovers from a transient polling failure and finishes normally', async () => {
  let requests = 0;
  const h = harness(async () => {
    if (++requests === 1) throw new Error('offline');
    return { status: requests === 2 ? 'running' : 'done' };
  });
  await flush();
  await h.tick();
  await h.tick();
  assert.deepEqual(h.updates.map(j => j.status), ['running', 'done']);
  assert.equal(h.finished.length, 1);
  assert.equal(h.errors.length, 0);
  assert.equal(h.timers.length, 0);
});

test('persistent failure stops after four attempts and reports failure', async () => {
  let requests = 0;
  const h = harness(async () => { requests++; throw new Error('offline'); });
  await flush();
  for (let i = 0; i < 3; i++) await h.tick();
  assert.equal(requests, 4);
  assert.equal(h.errors.length, 1);
  assert.equal(h.finished.length, 0);
  assert.equal(h.timers.length, 0);
});

test('cancelling ignores an in-flight response', async () => {
  let resolve;
  const h = harness(() => new Promise(r => { resolve = r; }));
  h.cancel();
  resolve({ status: 'done' });
  await flush();
  assert.deepEqual([h.updates, h.finished, h.errors, h.timers], [[], [], [], []]);
});

test('cancelling removes a scheduled retry', async () => {
  let requests = 0;
  const h = harness(async () => { requests++; throw new Error('offline'); });
  await flush();
  h.cancel();
  await h.tick();
  assert.equal(requests, 1);
  assert.equal(h.errors.length, 0);
});

test('backend job failure terminates polling without retrying the job', async () => {
  const h = harness(async () => ({ status: 'error', error: 'Mailbox expired' }));
  await flush();
  assert.equal(h.finished[0].status, 'error');
  assert.equal(h.timers.length, 0);
});

test('a forever-running job eventually stops checking', async () => {
  let clock = 0;
  const h = harness(async () => ({ status: 'running' }), { now: () => clock });
  await flush();
  clock = 15 * 60_000;
  await h.tick();
  assert.equal(h.errors.length, 1);
  assert.match(h.errors[0].message, /timed out/);
  assert.equal(h.timers.length, 0);
});
