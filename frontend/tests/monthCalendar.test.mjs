import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const exports = {};
const code = ts.transpileModule(readFileSync(new URL('../src/components/MonthCalendar.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2023 },
}).outputText;
vm.runInNewContext(code, { exports, require });
const { monthDays, dateKey, parseDay } = exports;

test('month grid starts Monday and covers month boundaries with complete weeks', () => {
  const days = monthDays(parseDay('2026-10-01'));
  assert.equal(dateKey(days[0]), '2026-09-28');
  assert.equal(dateKey(days.at(-1)), '2026-11-01');
  assert.equal(days.length % 7, 0);
  assert.equal(days[0].getDay(), 1);
  assert.equal(new Set(days.map(dateKey)).size, days.length);
});

test('leap February and six-week months include every date', () => {
  assert.equal(monthDays(parseDay('2028-02-01')).filter(day => day.getMonth() === 1).length, 29);
  assert.equal(monthDays(parseDay('2026-03-01')).length, 42);
  assert.equal(dateKey(parseDay('2026-12-31')), '2026-12-31');
});
