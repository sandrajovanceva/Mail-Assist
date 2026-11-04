import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const code = ts.transpileModule(readFileSync(new URL('../src/chatStream.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const { readChatEvents } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const bytes = new TextEncoder();

test('delivers chunks before completion and handles split UTF-8 and JSON lines', async () => {
  let controller;
  const stream = new ReadableStream({ start(value) { controller = value; } });
  const events = [];
  const reading = readChatEvents(stream, event => events.push(event));
  const first = bytes.encode('{"type":"delta","text":"Здраво"}\n');
  for (const byte of first) controller.enqueue(new Uint8Array([byte]));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(events[0].text, 'Здраво');
  assert.equal(events.length, 1);
  controller.enqueue(bytes.encode('{"type":"done","answer":"Здраво","suggested_reply":null,"draft_id":null}\n'));
  controller.close(); await reading;
  assert.equal(events[1].type, 'done');
});

test('reports provider failures and truncated streams instead of completing', async () => {
  for (const text of ['{"type":"error","message":"Provider busy"}\n', '{"type":"delta","text":"Partial"}\n']) {
    const stream = new ReadableStream({ start(controller) { controller.enqueue(bytes.encode(text)); controller.close(); } });
    await assert.rejects(readChatEvents(stream, () => {}), /Provider busy|interrupted/);
  }
});
