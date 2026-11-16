import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const storage = new Map();
globalThis.localStorage = {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: key => storage.delete(key),
};
globalThis.window = { location: { reload() {} } };
const source = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8')
  .replaceAll('import.meta.env', '({})');
const streamCode = ts.transpileModule(readFileSync(new URL('../src/chatStream.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const streamUrl = `data:text/javascript;base64,${Buffer.from(streamCode).toString('base64')}`;
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText.replace('"./chatStream"', JSON.stringify(streamUrl));
const { api, auth } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('sidebar counts use one small aggregate request', async () => {
  auth.setToken('counts');
  let calls = 0;
  globalThis.fetch = async url => {
    calls++;
    assert.equal(new URL(url).pathname,'/emails/counts');
    return {ok:true,json:async()=>({all:10000,routine:5000,attention:2,reply:4})};
  };
  assert.equal((await api.emailCounts()).all,10000);
  assert.equal(calls,1);
});

test('Google connection starts with bearer POST and browser cookies, without token in URL', async () => {
  auth.setToken('private-session');
  let target;
  window.location.assign = url => { target = url; };
  globalThis.fetch = async (url, options) => {
    assert.equal(new URL(url).pathname, '/auth/google/login');
    assert.equal(new URL(url).searchParams.get('calendar'), 'true');
    assert.equal(new URL(url).searchParams.has('token'), false);
    assert.equal(options.method, 'POST');
    assert.equal(options.credentials, 'include');
    assert.equal(options.headers.Authorization, 'Bearer private-session');
    return { ok: true, json: async () => ({ url: 'https://accounts.google.com/authorize' }) };
  };
  await api.connectGoogle(true);
  assert.equal(target, 'https://accounts.google.com/authorize');
});

test('email and draft loading follows all pages', async () => {
  auth.setToken('first');
  for (const endpoint of ['emails', 'drafts']) {
    const offsets = [];
    globalThis.fetch = async url => {
      const query = new URL(url).searchParams;
      const offset = Number(query.get('offset'));
      offsets.push(offset);
      return { ok: true, json: async () => Array.from({ length: offset === 0 ? 200 : 35 }, (_, i) => ({ id: offset + i })) };
    };
    const rows = await api[endpoint]();
    assert.equal(rows.length, 235);
    assert.deepEqual(offsets, [0, 200]);
  }
});

test('logout aborts outstanding requests', async () => {
  auth.setToken('first');
  globalThis.fetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  });
  const pending = api.accounts();
  auth.clear();
  await assert.rejects(pending, /aborted/);
});

test('a response body finishing after account switching is rejected', async () => {
  auth.setToken('first');
  let finishBody;
  let bodyStarted;
  const started = new Promise(resolve => { bodyStarted = resolve; });
  globalThis.fetch = async () => ({ ok: true, json: () => {
    bodyStarted();
    return new Promise(resolve => { finishBody = resolve; });
  } });
  const pending = api.accounts();
  await started;
  auth.setToken('second');
  finishBody([{ email_address: 'first@example.com' }]);
  await assert.rejects(pending, /Session changed/);
});
