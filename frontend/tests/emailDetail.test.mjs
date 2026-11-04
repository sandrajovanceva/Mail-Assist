import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const flush = () => new Promise(resolve => setImmediate(resolve));

// Exercise the actual component's state/effect callbacks without a DOM. This
// deliberately does not claim to test browser rendering or React scheduling.
function editor(api, componentName = 'EmailDetail') {
  const slots = [];
  let cursor = 0;
  let pending = [];
  const hooks = {
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, value => {
        slots[index].value = typeof value === 'function' ? value(slots[index].value) : value;
      }];
    },
    useRef(value) {
      const index = cursor++;
      slots[index] ??= { current: value };
      return slots[index];
    },
    useEffect(callback, deps) {
      const index = cursor++;
      const previous = slots[index];
      if (!previous || deps.some((dep, i) => !Object.is(dep, previous.deps[i]))) {
        pending.push(() => {
          previous?.cleanup?.();
          slots[index] = { deps, cleanup: callback() };
        });
      }
    },
  };
  const code = ts.transpileModule(readFileSync(new URL(`../src/components/${componentName}.tsx`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2023 },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    exports: module.exports, module,
    window: new EventTarget(),
    require: name => name === 'react' ? hooks : name === '../api' ? { api }
      : name === './Icons' ? { ArchiveIcon: () => null }
      : name === './DraftPreferences' ? { DraftPreferences: () => null }
      : name === './CalendarPage' ? { CalendarPage: () => null }
      : name === './CalendarSelect' ? { CalendarSelect: () => null }
      : name === './CalendarDateTime' ? { CalendarDateTime: () => null }
      : name === './MonthCalendar' ? (() => {
        const exports = {};
        const calendarCode = ts.transpileModule(readFileSync(new URL('../src/components/MonthCalendar.tsx', import.meta.url), 'utf8'), {
          compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2023 },
        }).outputText;
        vm.runInNewContext(calendarCode, { exports, require: name => name === 'react' ? hooks : require(name) });
        return exports;
      })() : require(name),
  });
  return {
    render(props) {
      cursor = 0;
      pending = [];
      const tree = module.exports[componentName](props);
      pending.forEach(effect => effect());
      return tree;
    },
    dispose() { slots.forEach(slot => slot?.cleanup?.()); },
    values: () => slots.filter(slot => slot && 'value' in slot).map(slot => slot.value),
  };
}

function find(node, predicate) {
  if (!node || typeof node !== 'object') return undefined;
  if (predicate(node)) return node;
  const children = node.props?.children;
  for (const child of [children].flat(Infinity)) {
    const found = find(child, predicate);
    if (found) return found;
  }
}

const email = { id: 1, subject: 'Meeting', sender: 'Alex', body_text: 'Tuesday?', received_at: '2026-10-20', summary: '' };
const draft = { id: 9, email_id: 1, content: 'Original reply', original_content: 'Original reply', status: 'pending' };
const props = { email, defaultTone: 'professional', onError: () => {}, onArchive: () => {} };

test('calendar passes selected checked times to drafting and clears stale results on changes', async () => {
  const picked = [];
  const slot = { start: '2030-01-07T10:00:00+01:00', end: '2030-01-07T10:30:00+01:00' };
  const component = editor({
    calendarLoginUrl: () => '/connect',
    calendarStatus: async () => ({ connected: true, email: 'owner@gmail.com' }),
    availability: async () => ({ slots: [slot], timezone: 'Europe/Skopje', checked_at: '2030-01-07T08:00:00Z' }),
  }, 'CalendarPage');
  const options = { onChoose: text => picked.push(text) };
  component.render(options);
  await flush();
  let tree = component.render(options);
  await find(tree, n => n.type === 'form').props.onSubmit({ preventDefault() {} });
  tree = component.render(options);
  find(tree, n => n.props?.className === 'calendar-slots').props.children[0].props.onClick();
  tree = component.render(options);
  const useTimes = find(tree, n => n.type === 'button' && n.props.children === 'Use selected times in draft');
  assert.equal(useTimes.props.disabled, false);
  await useTimes.props.onClick();
  assert.match(picked[0], /Europe\/Skopje/);
  assert.match(picked[0], /Do not claim a meeting is booked/);
  find(tree, n => n.props?.kind === 'date').props.onChange('2030-01-08');
  tree = component.render(options);
  assert.equal(find(tree, n => n.props?.className === 'calendar-results'), undefined);
  component.dispose();
});

test('calendar lookup failure is displayed without offering available times', async () => {
  const component = editor({ calendarLoginUrl: () => '/connect',
    calendarStatus: async () => ({ connected: true }),
    availability: async () => { throw new Error('Calendar unavailable'); },
  }, 'CalendarPage');
  component.render({}); await flush();
  let tree = component.render({});
  await find(tree, n => n.type === 'form').props.onSubmit({ preventDefault() {} });
  tree = component.render({});
  assert.match(find(tree, n => n.props?.role === 'alert').props.children, /Calendar unavailable/);
  assert.equal(find(tree, n => n.props?.className === 'calendar-results'), undefined);
  component.dispose();
});

test('refreshing the same email preserves unsaved draft text and does not reload drafts', async () => {
  let loads = 0;
  const component = editor({ draftsFor: async () => { loads++; return [draft]; } });
  component.render(props);
  await flush();
  let tree = component.render(props);
  find(tree, n => n.type === 'textarea').props.onChange({ target: { value: 'My unsaved wording' } });
  const refreshed = { ...props, email: { ...email, is_read: true, summary: 'Updated metadata' } };
  component.render(refreshed);
  tree = component.render(refreshed);
  assert.equal(find(tree, n => n.type === 'textarea').props.value, 'My unsaved wording');
  assert.equal(loads, 1);
  component.dispose();
});

test('a late draft response is ignored after leaving the email', async () => {
  let resolve;
  const component = editor({ draftsFor: () => new Promise(r => { resolve = r; }) });
  component.render(props);
  component.dispose();
  resolve([draft]);
  await flush();
  assert.equal(component.values().includes(draft), false);
  assert.equal(component.values().includes('Original reply'), false);
});

test('saved default tone updates the editor until the user explicitly chooses a tone', async () => {
  const component = editor({ draftsFor: async () => [] });
  component.render(props);
  await flush();
  const updated = { ...props, defaultTone: 'friendly' };
  component.render(updated);
  let tree = component.render(updated);
  assert.equal(find(tree, n => n.type === 'select').props.value, 'friendly');
  find(tree, n => n.type === 'select').props.onChange({ target: { value: 'casual' } });
  const next = { ...props, defaultTone: 'formal' };
  component.render(next);
  tree = component.render(next);
  assert.equal(find(tree, n => n.type === 'select').props.value, 'casual');
  component.dispose();
});

test('reopening settings refreshes learning examples without replacing unsaved preferences', async () => {
  let examples = [];
  let settingsLoads = 0;
  const component = editor({
    settings: async () => { settingsLoads++; return { user_name: 'Sandra', signature: '', default_tone: 'professional', style_profile: '', auto_send_categories: 'routine' }; },
    knowledge: async () => [],
    draftEdits: async () => examples,
  }, 'SettingsPage');
  const settingsProps = { active: true, onError: error => { throw new Error(error); } };
  component.render(settingsProps);
  await flush();
  let tree = component.render(settingsProps);
  find(tree, n => n.type === 'input' && n.props.id === 'settings-name').props.onChange({ target: { value: 'Unsaved name' } });
  component.render({ ...settingsProps, active: false });
  examples = [{ id: 1, applied: false, subject: 'Presentation', original: 'Long reply', edited: 'Short reply' }];
  component.render(settingsProps);
  await flush();
  tree = component.render(settingsProps);
  assert.equal(find(tree, n => n.type === 'input' && n.props.id === 'settings-name').props.value, 'Unsaved name');
  assert.ok(find(tree, n => n.type === 'pre' && n.props.children === 'Short reply'));
  assert.equal(settingsLoads, 1);
  component.dispose();
});
