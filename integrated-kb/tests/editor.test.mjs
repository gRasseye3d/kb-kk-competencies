// Run: node --experimental-vm-modules --test tests/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

class Node {
  constructor(tag, attrs = {}) {
    this.tag = tag; this.children = []; this.events = {}; this.style = {};
    this.attrs = attrs; this.value = attrs.value || ''; this.disabled = attrs.disabled != null;
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith('on')) this.addEventListener(key.slice(2), value);
    }
  }
  append(...children) { this.children.push(...children.flat().filter(c => c != null && c !== false)); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  addEventListener(type, fn) { (this.events[type] ||= []).push(fn); }
  get textContent() { return this.children.map(c => c instanceof Node ? c.textContent : String(c)).join(''); }
  set textContent(value) { this.replaceChildren(value); }
}
const el = (tag, attrs, ...children) => { const node = new Node(tag, attrs); node.append(...children); return node; };
const all = (root, tag) => [root, ...root.children.filter(c => c instanceof Node).flatMap(c => all(c, tag))]
  .filter(n => !tag || n.tag === tag);
const button = (root, label) => all(root, 'button').find(n => n.textContent === label);
const fire = async (node, event = 'click') => {
  assert.ok(node);
  // Intentionally exercise handlers even for disabled controls: they must guard POST too.
  for (const fn of node.events[event] || []) await fn();
};

async function harness({ spaces = [], tree = [], existing = null, error = null } = {}) {
  const calls = [], alerts = [], routes = [];
  const main = el('div'), modal = el('div');
  const context = vm.createContext({ alert: text => alerts.push(text), history: { back() {} },
    document: { getElementById: () => modal } });
  const state = { cache: {} };
  const modules = {
    '../app.js': { state, $, el, api, navigate: hash => routes.push(hash), loadTree: async () => {} },
    '../editor.js': { createEditor: () => ({ toolbar: el('div'), root: el('div'), getHTML: () => '<p>Не потерять</p>' }) },
    '../labels.js': { renderLabelsField: () => ({ node: el('div'), getLabels: () => [] }) }
  };
  function $(selector) { assert.equal(selector, '#main'); return main; }
  async function api(method, url, body) {
    calls.push({ method, url, body });
    if (method === 'GET' && url === '/api/admin/spaces') return spaces;
    if (method === 'GET' && url === '/api/tree') return tree;
    if (error) throw new Error(error);
    return { id: 'created' };
  }
  const cache = new Map();
  async function load(spec) {
    if (!cache.has(spec)) {
      assert.ok(modules[spec], spec);
      const exports = modules[spec];
      const mod = new vm.SyntheticModule(Object.keys(exports), function () {
        for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
      }, { context });
      await mod.link(() => {}); await mod.evaluate(); cache.set(spec, mod);
    }
    return cache.get(spec);
  }
  const source = await readFile(new URL('../static/js/views/editor.js', import.meta.url), 'utf8');
  const mod = new vm.SourceTextModule(source, { context, importModuleDynamically: load });
  await mod.link(load); await mod.evaluate();
  const root = await mod.namespace.renderEditor(existing);
  await fire(button(root, '⚙️ Настройки'));
  const [space, parent] = all(modal, 'select');
  return { root, space, parent, calls, alerts, routes };
}

test('new editor offers only explicitly writable spaces, preserves create then moderation', async () => {
  const h = await harness({ spaces: [
    { id: 'read', name: 'Чтение', can_create: false },
    { id: 'unknown', name: 'Без признака' },
    { id: 'edit', name: 'Редактор', can_create: true }
  ] });
  assert.deepEqual(h.space.children.map(n => n.value), ['edit']);
  assert.equal(h.space.value, 'edit');
  assert.equal(h.space.disabled, false);
  assert.equal(button(h.root, 'Сохранить').disabled, false);
  await fire(button(h.root, '📤 На модерацию'));
  const writes = h.calls.filter(c => c.method === 'POST');
  assert.deepEqual(writes.map(c => c.url), ['/api/pages', '/api/moderation/submit/created']);
  assert.equal(writes[0].body.space_id, 'edit');
  assert.equal(writes[0].body.parent_id, null);
  assert.deepEqual(h.routes, ['#/page/created']);
});

test('empty or read-only spaces explain permissions, disable both actions and never POST', async () => {
  for (const spaces of [[], [{ id: 'read', name: 'Чтение', can_create: false }], [{ id: 'old', name: 'Без признака' }]]) {
    const h = await harness({ spaces });
    assert.match(h.root.textContent, /Нет пространств.*права редактора/);
    for (const label of ['Сохранить', '📤 На модерацию']) {
      assert.equal(button(h.root, label).disabled, true);
      await fire(button(h.root, label));
    }
    assert.equal(h.calls.filter(c => c.method !== 'GET').length, 0);
  }
});

test('changing space resets parent to root and replaces options before save', async () => {
  const h = await harness({ spaces: ['a', 'b'].map(id => ({ id, name: id, can_create: true })),
    tree: [{ id: 'pa', space_id: 'a', title: 'A' }, { id: 'pb', space_id: 'b', title: 'B' }] });
  assert.deepEqual(h.parent.children.map(n => n.value), ['', 'pa']);
  h.parent.value = 'pa'; h.space.value = 'b'; await fire(h.space, 'change');
  assert.equal(h.parent.value, '');
  assert.deepEqual(h.parent.children.map(n => n.value), ['', 'pb']);
  await fire(button(h.root, 'Сохранить'));
  const write = h.calls.find(c => c.method === 'POST');
  assert.equal(write.body.space_id, 'b'); assert.equal(write.body.parent_id, null);
});

test('existing article keeps its space fixed, excludes itself from parents and enforces can_edit', async () => {
  for (const can_edit of [true, false, undefined]) {
    const h = await harness({ spaces: [
      { id: 'a', name: 'A', can_create: true }, { id: 'b', name: 'B', can_create: true }],
      existing: { id: 'page', space_id: 'b', parent_id: 'parent', can_edit },
      tree: [{ id: 'page', space_id: 'b', title: 'Self' }, { id: 'parent', space_id: 'b', title: 'Parent' }] });
    assert.equal(h.space.disabled, true); assert.equal(h.space.value, 'b');
    assert.deepEqual(h.space.children.map(n => n.value), ['b']);
    assert.deepEqual(h.parent.children.map(n => n.value), ['', 'parent']);
    assert.equal(h.parent.value, 'parent');
    assert.equal(button(h.root, 'Сохранить').disabled, can_edit !== true);
    // Even a programmatic select change must not attempt a move.
    h.space.value = 'a';
    await fire(button(h.root, 'Сохранить'));
    const writes = h.calls.filter(c => c.method !== 'GET');
    if (can_edit === true) {
      assert.equal(writes.length, 1); assert.equal(writes[0].method, 'PUT');
      assert.equal(writes[0].body.space_id, 'b');
    } else {
      assert.equal(writes.length, 0); assert.match(h.root.textContent, /Нет прав на редактирование/);
    }
  }
});

test('revoked write permission is explained in Russian, leaves content open and never submits', async () => {
  for (const error of ['Space access denied', 'Page access denied']) {
    const h = await harness({ spaces: [{ id: 'a', name: 'A', can_create: true }], error });
    await fire(button(h.root, '📤 На модерацию'));
    assert.match(h.alerts[0], /Нет прав на сохранение.*Скопируйте введённый текст/);
    assert.doesNotMatch(h.alerts[0], /access denied/);
    assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
    assert.equal(h.calls.some(c => c.url.startsWith('/api/moderation/')), false);
    assert.deepEqual(h.routes, []);
  }
});
