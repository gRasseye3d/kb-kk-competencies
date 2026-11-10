// Run: node --experimental-vm-modules --test tests/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.events = {}; this.disabled = false; }
  append(...nodes) { this.children.push(...nodes); }
  addEventListener(event, fn) { this.events[event] = fn; }
  set innerHTML(html) {
    this.html = html;
    if (this.tag === 'table') this.append(new Element('tbody'));
    if (this.tag === 'tr') this.append(new Element('td'));
  }
  querySelector(tag) { return this.children.find(child => child.tag === tag); }
  get lastElementChild() { return this.children.at(-1); }
}
function all(root, tag) { return [root, ...root.children.flatMap(child => all(child, tag))].filter(node => !tag || node.tag === tag); }
const source = await readFile(new URL('../static/js/moderation_v2.js', import.meta.url), 'utf8');
const appSource = await readFile(new URL('../static/js/app.js', import.meta.url), 'utf8');
const apiSource = appSource.slice(appSource.indexOf('export async function api('), appSource.indexOf('export async function loadTree('));

async function harness({ can_approve = true, can_reject = false, reason = 'self_review', getStatus = 200, postStatus = 200 } = {}) {
  const calls = [], alerts = [];
  let reloads = 0;
  const context = vm.createContext({
    document: { createElement: tag => new Element(tag) },
    location: { hash: '#/moderation', reload: () => reloads++ },
    alert: message => alerts.push(message), prompt: () => 'Нужны правки',
    state: { csrf: 'existing-session-token' },
    fetch: async (url, options) => {
      calls.push({ url, ...options });
      const status = options.method === 'GET' ? getStatus : postStatus;
      return { status, ok: status === 200, headers: { get: () => 'application/json' }, json: async () => status !== 200 ? { error: 'Forbidden or stale', code: status === 403 ? 'self_review' : 'approval_stale' } :
        options.method === 'GET' ? [{ id: 'request', page_id: 'page', status: 'pending', title: 'Example', author: 'Author', can_approve, can_reject, reason }] : { ok: true } };
    }
  });
  const api = new vm.SourceTextModule(apiSource, { context });
  await api.link(() => {});
  const mod = new vm.SourceTextModule(source, { context });
  await mod.link(spec => { assert.equal(spec, './app.js'); return api; });
  await mod.evaluate();
  const root = await mod.namespace.render();
  return { root, calls, alerts, get reloads() { return reloads; }, buttons: all(root, 'button') };
}

test('opening moderation only reads queue; admin self-approve uses existing CSRF', async () => {
  const h = await harness();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].method, 'GET');
  assert.equal(h.calls[0].url, '/api/moderation/queue');
  assert.equal(h.buttons[0].disabled, false);
  assert.equal(h.buttons[1].disabled, true);
  await h.buttons[0].events.click();
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].method, 'POST');
  assert.equal(h.calls[1].url, '/api/moderation/decide/request');
  assert.deepEqual(JSON.parse(h.calls[1].body), { action: 'approve', csrf: 'existing-session-token' });
  assert.equal(h.calls[1].credentials, 'same-origin');
  assert.equal(h.reloads, 1);
});

test('self moderator buttons stay disabled and cannot send decisions', async () => {
  const h = await harness({ can_approve: false });
  assert.equal(h.buttons.length, 2);
  for (const button of h.buttons) {
    assert.equal(button.disabled, true);
    assert.match(button.title, /собственн/);
    await button.events.click();
  }
  assert.equal(h.calls.length, 1);
});

test('other authorized moderator can reject with comment', async () => {
  const h = await harness({ can_reject: true, reason: null });
  assert.ok(h.buttons.every(button => !button.disabled));
  await h.buttons[1].events.click();
  assert.deepEqual(JSON.parse(h.calls[1].body), { action: 'reject', comment: 'Нужны правки', csrf: 'existing-session-token' });
});

test('stale decision displays localized conflict and blocks resubmission', async () => {
  const h = await harness({ can_reject: true, postStatus: 409 });
  await h.buttons[0].events.click();
  assert.match(h.alerts[0], /Заявка устарела.*Обновите очередь/);
  assert.ok(h.buttons.every(button => button.disabled));
  await h.buttons[0].events.click();
  assert.equal(h.calls.length, 2);
  assert.equal(h.reloads, 0);
});

test('forbidden decision shows localized self-review explanation', async () => {
  const h = await harness({ postStatus: 403 });
  await h.buttons[0].events.click();
  assert.match(h.alerts[0], /Нельзя проверять собственную статью/);
  assert.equal(h.reloads, 0);
});

test('queue HTTP errors show an error rather than pretending queue is empty', async () => {
  for (const getStatus of [401, 403, 404, 500]) {
    const h = await harness({ getStatus });
    assert.equal(h.calls.length, 1);
    assert.equal(h.buttons.length, 0);
    const error = all(h.root, 'p')[0];
    assert.equal(error.role, 'alert');
    assert.doesNotMatch(error.textContent, /Очередь пуста/);
  }
});

test('legacy decision 404 is not treated as success', async () => {
  const h = await harness({ postStatus: 404 });
  await h.buttons[0].events.click();
  assert.match(h.alerts[0], /не найдена/);
  assert.equal(h.reloads, 0);
});

test('moderation does not expose take or deletion actions', async () => {
  assert.doesNotMatch(source, /\/api\/moderation\/take|api\('DELETE'/);
  const h = await harness({ can_reject: true });
  assert.deepEqual(h.buttons.map(button => button.textContent), ['Одобрить', 'Отклонить']);
});
