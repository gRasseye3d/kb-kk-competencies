// Run: node --experimental-vm-modules --test tests/frontend.test.mjs
// Dependency-free DOM contract tests; no server or production data required.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

class Node {
  constructor(tag, attrs = {}) {
    this.tag = tag; this.attrs = {}; this.children = []; this.events = {};
    this.value = ''; this.checked = false; this.disabled = false;
    Object.entries(attrs).forEach(([key, value]) => {
      if (key.startsWith('on')) this.addEventListener(key.slice(2), value);
      else this.setAttribute(key, value);
    });
  }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'value') this.value = String(value); }
  append(...children) {
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      this.children.push(child);
      if (child instanceof Node) child.parent = this;
    }
  }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { this.parent.children = this.parent.children.filter(c => c !== this); }
  addEventListener(type, fn) { (this.events[type] ||= []).push(fn); }
  reportValidity() { return true; }
  get textContent() { return this.children.map(c => c instanceof Node ? c.textContent : String(c)).join(''); }
  set textContent(value) { this.replaceChildren(value); }
}
const el = (tag, attrs = {}, ...children) => { const node = new Node(tag, attrs); node.append(...children); return node; };
const all = (node, tag) => [node, ...node.children.filter(c => c instanceof Node).flatMap(c => all(c, tag))].filter(n => !tag || n.tag === tag);
const button = (root, text) => all(root, 'button').find(n => n.textContent === text);
const form = (root, text) => all(root, 'form').find(n => all(n, 'button').some(b => b.attrs.type === 'submit' && b.textContent === text));
async function fire(node, type) {
  assert.ok(node, `missing node for ${type}`);
  for (const fn of node.events[type] || []) await fn({ preventDefault() {}, currentTarget: node });
}
const uuid = '11111111-2222-4333-8444-555555555555';
const did = '21111111-2222-4333-8444-555555555555';
const cid = '31111111-2222-4333-8444-555555555555';

function deferred() {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

async function routerHarness(views, loadTree = async () => {}) {
  const source = await readFile(new URL('../static/js/app.js', import.meta.url), 'utf8');
  const routeSource = source.slice(source.indexOf('let isRendering = false;'), source.indexOf("$('#globalSearch').addEventListener"));
  const attached = [];
  const main = { style: {}, innerHTML: '', append(view) { attached.push(view); } };
  const location = { hash: '#/testing' };
  const state = { user: { roles: ['employee'] }, cache: { tree: [], spaces: [] } };
  const context = vm.createContext({ state, location, el, loadTree, renderTree() {},
    $: selector => selector === '#main' ? main : { style: {} },
    document: { querySelector: () => ({ style: {} }), querySelectorAll: () => [] } });
  const mod = new vm.SourceTextModule(routeSource, { context, importModuleDynamically: async spec => {
    assert.ok(views[spec], `unexpected view import: ${spec}`);
    const view = new vm.SyntheticModule(Object.keys(views[spec]), function () {
      for (const [name, fn] of Object.entries(views[spec])) this.setExport(name, fn);
    }, { context });
    await view.link(() => {});
    await view.evaluate();
    return view;
  } });
  await mod.link(() => {});
  await mod.evaluate();
  return { render: mod.namespace.render, location, state, attached };
}

test('router coalesces hashchanges during a delayed view and never attaches stale content', async () => {
  const started = deferred(), gate = deferred();
  const h = await routerHarness({
    './views/testing.js': { renderTesting: async () => { started.resolve(); return gate.promise; } },
    './views/profile.js': { renderProfilePage: async () => 'current profile' }
  });
  const pending = h.render();
  await started.promise;
  h.location.hash = '#/competencies'; await h.render();
  h.location.hash = '#/profile'; await h.render();
  gate.resolve('obsolete testing');
  await pending;
  assert.deepEqual(h.attached, ['current profile']);
});

test('router checks current hash after async tree load even before hashchange dispatch', async () => {
  const gate = deferred(), started = deferred();
  const h = await routerHarness({ './views/profile.js': { renderProfilePage: async () => 'profile' } }, async () => {
    started.resolve(); await gate.promise; h.state.cache.tree = []; h.state.cache.spaces = [];
  });
  h.state.cache.tree = null;
  const pending = h.render();
  await started.promise;
  h.location.hash = '#/profile';
  gate.resolve();
  await pending;
  assert.deepEqual(h.attached, ['profile']);
});

test('router retains same-route refresh requests while a view is pending', async () => {
  const gate = deferred(), started = deferred();
  let calls = 0;
  const h = await routerHarness({ './views/testing.js': { renderTesting: async () => {
    if (++calls > 1) return 'refreshed';
    started.resolve(); return gate.promise;
  } } });
  const pending = h.render();
  await started.promise;
  await h.render();
  gate.resolve('obsolete');
  await pending;
  assert.equal(calls, 2);
  assert.deepEqual(h.attached, ['refreshed']);
});

test('a rejected obsolete load does not lose the queued route or surface its stale error', async () => {
  const gate = deferred(), started = deferred();
  const h = await routerHarness({
    './views/testing.js': { renderTesting: async () => { started.resolve(); return gate.promise; } },
    './views/profile.js': { renderProfilePage: async () => 'profile' }
  });
  const pending = h.render();
  await started.promise;
  h.location.hash = '#/profile'; await h.render();
  gate.reject(new Error('obsolete network failure'));
  await pending;
  assert.deepEqual(h.attached, ['profile']);
});

async function harness(respond, { roles = ['employee'], hash = '#/competencies' } = {}) {
  const calls = [], routes = [];
  const location = { hash };
  const context = vm.createContext({ URLSearchParams, location, history: { replaceState(_a, _b, hash) { location.hash = hash; } } });
  const state = { user: { id: uuid, roles } };
  const app = new vm.SyntheticModule(['el', 'state', 'api', 'navigate'], function () {
    this.setExport('el', el); this.setExport('state', state);
    this.setExport('api', async (method, url, body) => { calls.push({ method, url, body }); return respond(method, url, body); });
    this.setExport('navigate', hash => { routes.push(hash); location.hash = hash; });
  }, { context });
  const modules = new Map();
  async function load(name) {
    if (modules.has(name)) return modules.get(name);
    const source = await readFile(new URL(`../static/js/views/${name}`, import.meta.url), 'utf8');
    const mod = new vm.SourceTextModule(source, { context });
    modules.set(name, mod);
    await mod.link(spec => spec === '../app.js' ? app : load(spec.replace('./', '')));
    return mod;
  }
  const competencies = await load('competencies.js');
  await competencies.evaluate();
  return { testing: modules.get('testing.js').namespace, competencies: competencies.namespace, calls, routes, location };
}

test('named result cards group competencies, never show identifier fallbacks, start keeps attempt route', async () => {
  const h = await harness(() => ({ id: cid }));
  const rows = [
    { assignment_id: uuid, test_title: 'Вводный тест', status: 'completed', competency_id: cid, competency_name: 'Коммуникация', level: 2, correct_count: 2, total_count: 2 },
    { assignment_id: uuid, test_title: 'Вводный тест', status: 'completed', competency_id: did, competency_name: 'Аналитика', level: 1, correct_count: 1, total_count: 2 }
  ];
  const cards = h.testing.resultCards(rows, true);
  assert.equal(all(cards, 'section').length, 1);
  assert.match(cards.textContent, /Вводный тест.*Коммуникация.*Аналитика/);
  assert.ok(!cards.textContent.includes(uuid));
  assert.ok(!cards.textContent.includes(cid));
  const assigned = h.testing.resultCards([{ assignment_id: uuid, status: 'assigned' }], true);
  await fire(form(assigned, 'Начать тест'), 'submit');
  assert.deepEqual(h.routes, [`#/testing?attempt=${cid}`]);
  const progress = h.testing.resultCards([{ assignment_id: uuid, status: 'in_progress', attempt_id: cid }], true);
  assert.equal(all(progress, 'a')[0].attrs.href, `#/testing?attempt=${cid}`);
});

test('API transport preserves structured conflicts and legacy error messages', async () => {
  const source = await readFile(new URL('../static/js/app.js', import.meta.url), 'utf8');
  const apiSource = source.slice(source.indexOf('export async function api('), source.indexOf('export async function loadTree('));
  for (const payload of [{ error: 'stale' }, { error: 'No competencies currently available',
    code: 'assessment_unavailable', details: { competencies: [] } }]) {
    const context = vm.createContext({ state: {}, fetch: async () => ({ ok: false, status: 409, json: async () => payload }) });
    const mod = new vm.SourceTextModule(apiSource, { context });
    await mod.link(() => {}); await mod.evaluate();
    await assert.rejects(mod.namespace.api('POST', '/api/testing/assignments/start', {}), error => {
      assert.equal(error.status, 409); assert.equal(error.message, payload.error);
      assert.equal(error.code, payload.code); assert.equal(error.details, payload.details);
      return true;
    });
  }
});

test('unavailable start explains named cooldown dates and busy attempts without stale-state messaging', async () => {
  for (const reason of ['cooldown_30d', 'reserved_active']) {
    const h = await harness(() => { throw Object.assign(new Error('No competencies currently available'), {
      status: 409, code: 'assessment_unavailable', details: { competencies: [
        { competency_id: cid, competency_name: '<Аналитика>', reason,
          available_at: reason === 'cooldown_30d' ? '2090-01-01T12:00:00+00:00' : null },
        { competency_id: did, competency_name: 'Общение', reason, available_at: null }
      ] }
    }); });
    const root = h.testing.resultCards([{ assignment_id: uuid, status: 'assigned' }], true);
    await fire(form(root, 'Начать тест'), 'submit');
    const message = all(root, 'p').find(n => n.attrs.role === 'status').textContent;
    assert.match(message, /Начать тест пока нельзя.*<Аналитика>.*Общение/);
    assert.doesNotMatch(message, /Данные изменились|Не указан|Invalid Date/);
    assert.ok(!root.textContent.includes(cid));
    assert.equal(all(root).some(n => n.attrs.html), false);
    assert.deepEqual(h.routes, []);
    assert.equal(button(root, 'Начать тест').disabled, false);
    if (reason === 'cooldown_30d') {
      assert.match(root.textContent, /период ожидания 30 дней.*доступна с/);
      assert.ok(root.textContent.includes(h.testing.dateLabel('2090-01-01T12:00:00+00:00')));
    } else {
      assert.match(root.textContent, /другая незавершённая попытка.*Завершите её/);
      assert.doesNotMatch(root.textContent, /доступна с|30 дней|2090/);
    }
  }
});

test('attempt shows named exclusions, validates every answer, submits option IDs only', async () => {
  const h = await harness((method) => method === 'GET' ? {
    status: 'in_progress', test_title: 'Проверка навыков',
    competencies: [{ competency_id: cid, competency_name: 'Аналитика', state: 'included' },
      { competency_id: did, competency_name: 'Общение', state: 'excluded', exclusion_reason: 'reserved_active' }],
    questions: [{ id: uuid, type: 'single', body: 'Выберите ответ', options: [{ id: cid, body: 'Ответ А' }, { id: did, body: 'Ответ Б' }] }]
  } : { results: [] }, { hash: `#/testing?attempt=${uuid}` });
  const root = await h.testing.renderTesting();
  assert.match(root.textContent, /Проверка навыков.*Аналитика.*Общение.*незавершённая попытка/);
  assert.ok(!root.textContent.includes(uuid));
  const submit = form(root, 'Отправить ответы');
  await fire(submit, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  assert.match(root.textContent, /Ответьте на каждый вопрос/);
  all(submit, 'input')[0].checked = true;
  await fire(submit, 'submit');
  assert.equal(JSON.stringify(h.calls.at(-1).body), JSON.stringify({ answers: { [uuid]: [cid] } }));
  assert.deepEqual(h.routes, ['#/testing']);
});

test('manager assignments use named member/published test selects and timezone deadline', async () => {
  const h = await harness((_method, url) => url.endsWith('/members') ? [{ id: uuid, display_name: 'Анна Иванова', login: 'anna' }] :
    url.endsWith('/tests') ? [{ id: cid, title: 'Аналитика', version: 1, status: 'published' }, { id: did, title: 'Черновой тест', status: 'draft' }] : [], { roles: ['manager'] });
  const root = await h.testing.renderManagerTesting({ id: did }, async () => {});
  assert.match(root.textContent, /Анна Иванова/);
  assert.ok(!root.textContent.includes('Черновой тест'));
  assert.ok(!root.textContent.includes(uuid));
  const assign = form(root, 'Назначить');
  const selects = all(assign, 'select');
  selects[0].value = uuid; selects[1].value = cid;
  all(assign, 'input')[0].value = '2099-01-01T12:00';
  await fire(assign, 'submit');
  const payload = h.calls.at(-1).body;
  assert.equal(payload.user_id, uuid); assert.equal(payload.test_id, cid);
  assert.match(payload.due_at, /Z$/);
});

test('KK uses active-map names, validates test selection, shows correctness and both moderation decisions', async () => {
  const h = await harness((method, url) => {
    if (method !== 'GET') return {};
    if (url.endsWith('/questions')) return [{ id: uuid, competency_id: cid, body: 'Вопрос', moderation_status: 'pending' }];
    if (url.endsWith(`/questions/${uuid}`)) return { options: [{ body: 'Правильно', is_correct: true }, { body: 'Неправильно', is_correct: false }] };
    return [{ id: did, title: 'Новый тест', status: 'draft', competencies: [{ id: cid, name: 'Аналитика' }] }];
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderResponsibleTesting([{ competency_id: cid, name: 'Аналитика' }, { competency_id: did, name: 'Общение' }], async () => {});
  assert.ok(!root.textContent.includes(uuid));
  assert.ok(button(root, 'Одобрить')); assert.ok(button(root, 'Отклонить'));
  await fire(form(root, 'Варианты и ключ'), 'submit');
  assert.match(root.textContent, /ПравильноВерный.*НеправильноНеверный/);
  const create = form(root, 'Создать тест');
  all(create, 'input')[0].value = 'Проверка';
  await fire(create, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  all(create, 'input').filter(n => n.attrs.type === 'checkbox').forEach(n => n.checked = true);
  await fire(create, 'submit');
  assert.equal(JSON.stringify(h.calls.at(-1).body.competency_ids), JSON.stringify([cid, did]));
  await fire(form(root, 'Опубликовать'), 'submit');
  assert.equal(h.calls.at(-1).url, `/api/testing/tests/${did}/publish`);
  await fire(form(root, 'Отклонить'), 'submit');
  assert.equal(h.calls.at(-1).body.decision, 'reject');
});

test('approval sends active ID/version; 409 blocks retry until explicit refresh', async () => {
  let activeVersion = 1;
  const h = await harness((method, url) => {
    if (method === 'POST') { const error = new Error('Active profile changed'); error.status = 409; throw error; }
    if (url.endsWith('/departments')) return [{ id: did, name: 'Отдел', manager_id: uuid }];
    if (url.endsWith('/profiles')) return [{ id: cid, name: 'Карта', version: 2, status: 'pending_approval', competencies: [] }];
    if (url.endsWith('/active')) return { id: uuid, version: activeVersion };
    return [];
  }, { roles: ['employee', 'manager', 'kk_responsible', 'admin'], hash: '#/competencies?tab=manager' });
  const root = await h.competencies.renderCompetencies();
  assert.equal(all(root, 'button').filter(b => b.attrs.role === 'tab').length, 4);
  await fire(form(root, 'Утвердить карту'), 'submit');
  const approval = h.calls.find(c => c.method === 'POST');
  assert.equal(approval.body.expected_active_id, uuid);
  assert.equal(approval.body.expected_active_version, 1);
  assert.equal(button(root, 'Утвердить карту').disabled, true);
  assert.match(root.textContent, /Данные изменились\. Обновите их/);
  assert.doesNotMatch(root.textContent, /Начать тест пока нельзя/);
  await fire(form(root, 'Утвердить карту'), 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
  activeVersion = 2;
  await fire(button(root, 'Обновить данные'), 'click');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
  assert.equal(button(root, 'Утвердить карту').disabled, false);
  await fire(form(root, 'Утвердить карту'), 'submit');
  assert.equal(h.calls.at(-1).body.expected_active_version, 2);
});

test('employee tab does not call privileged APIs, even for multiple roles', async () => {
  const h = await harness(() => [], { roles: ['employee', 'manager', 'kk_responsible'] });
  const root = await h.competencies.renderCompetencies();
  assert.match(root.textContent, /Моя карта развития/);
  assert.deepEqual(h.calls.map(c => c.url), ['/api/competencies/mine', '/api/testing/results']);
});

test('manual question form validates the single-answer key before sending options', async () => {
  const h = await harness(method => method === 'GET' ? [] : {});
  const root = await h.testing.renderResponsibleTesting([{ competency_id: cid, name: 'Аналитика' }], async () => {});
  const create = form(root, 'Сохранить вопрос');
  all(create, 'select')[0].value = cid;
  all(create, 'textarea')[0].value = 'Как проверить гипотезу?';
  const texts = all(create, 'input').filter(n => !n.attrs.type);
  texts[0].value = 'Проверить данные'; texts[1].value = 'Угадать';
  const keys = all(create, 'input').filter(n => n.attrs.type === 'checkbox');
  await fire(create, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  keys.forEach(n => n.checked = true);
  await fire(create, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  keys[1].checked = false;
  await fire(create, 'submit');
  const payload = h.calls.at(-1).body;
  assert.equal(payload.competency_id, cid);
  assert.equal(payload.type, 'single');
  assert.equal(JSON.stringify(payload.options), JSON.stringify([
    { body: 'Проверить данные', correct: true }, { body: 'Угадать', correct: false }
  ]));
});

test('returning a map requires a nonblank note and keeps the existing review contract', async () => {
  const h = await harness((method, url) => {
    if (method === 'POST') return {};
    if (url.endsWith('/departments')) return [{ id: did, name: 'Отдел', manager_id: uuid }];
    if (url.endsWith('/profiles')) return [{ id: cid, name: 'Карта', version: 2, status: 'pending_approval', competencies: [] }];
    if (url.endsWith('/active')) return { id: null, version: null };
    return [];
  }, { roles: ['manager'], hash: '#/competencies?tab=manager' });
  const root = await h.competencies.renderCompetencies();
  const review = form(root, 'Вернуть на доработку');
  all(review, 'textarea')[0].value = '   ';
  await fire(review, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  all(review, 'textarea')[0].value = ' Уточните целевые уровни ';
  await fire(review, 'submit');
  const call = h.calls.find(c => c.method === 'POST');
  assert.equal(call.url, `/api/competencies/profiles/${cid}/review`);
  assert.equal(JSON.stringify(call.body), JSON.stringify({ decision: 'return', note: 'Уточните целевые уровни' }));
});

test('module treats legacy 404 response values as failures', async () => {
  const h = await harness(() => ({ error: 'Not Found', status: 404 }));
  await assert.rejects(h.testing.request('POST', '/api/testing/questions', {}), /Данные не найдены/);
});

test('competency admin assigns through eligible named selects and can clear the manager', async () => {
  const h = await harness((method, url) => {
    if (method !== 'GET') return {};
    if (url.endsWith('/catalog')) return { categories: [], competencies: [] };
    if (url.endsWith('/users')) return [
      { id: uuid, full_name: 'Анна Иванова', login: 'anna', department_id: did, roles: ['manager', 'kk_responsible'], status: 'active' },
      { id: cid, full_name: 'Другой отдел', department_id: cid, roles: ['manager'], status: 'active' }
    ];
    if (url.endsWith('/departments')) return [{ id: did, name: 'Аналитика', is_active: true, manager_id: uuid, responsible_ids: [uuid] }];
    return [];
  }, { roles: ['admin'], hash: '#/competencies?tab=admin' });
  const root = await h.competencies.renderCompetencies();
  assert.match(root.textContent, /Анна Иванова/);
  assert.ok(!root.textContent.includes(uuid));
  assert.ok(!root.textContent.includes('Другой отдел'));
  const manager = form(root, 'Сохранить руководителя');
  all(manager, 'select')[0].value = '';
  await fire(manager, 'submit');
  const update = h.calls.find(c => c.method === 'PUT');
  assert.equal(update.body.manager_id, null);
  const responsible = form(root, 'Назначить ответственного');
  all(responsible, 'select')[0].value = uuid;
  await fire(responsible, 'submit');
  assert.equal(h.calls.find(c => c.method === 'POST').body.user_id, uuid);
});

test('module contains no prompt/alert and admin has no raw identifier fallback', async () => {
  for (const file of ['competencies.js', 'testing.js', 'admin.js']) {
    const source = await readFile(new URL(`../static/js/views/${file}`, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /\b(?:prompt|alert|confirm)\s*\(/);
    assert.doesNotMatch(source, /['"]UUID:/);
    assert.doesNotMatch(source, /\|\|\s*uid\b/);
  }
});

test('shared transport preserves authenticated CSRF and exposes 409 without retry', async () => {
  const source = await readFile(new URL('../static/js/app.js', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('export async function api('), source.indexOf('export async function loadTree(')).replace('export ', '');
  const requests = [];
  const api = new Function('fetch', 'state', `${body}; return api;`)(async (url, options) => {
    requests.push({ url, options });
    return { status: 409, ok: false, json: async () => ({ error: 'Conflict' }) };
  }, { csrf: 'csrf-secret' });
  await assert.rejects(api('POST', '/api/example', {}), error => error.status === 409);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.credentials, 'same-origin');
  assert.equal(JSON.parse(requests[0].options.body).csrf, 'csrf-secret');
});
