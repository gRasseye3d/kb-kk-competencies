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
  const modalRoot = el('div');
  const context = vm.createContext({ URLSearchParams, location, document: { getElementById: () => modalRoot },
    history: { replaceState(_a, _b, hash) { location.hash = hash; } } });
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
    const mod = new vm.SourceTextModule(source, { context, importModuleDynamically: async spec => {
      assert.equal(spec, '../highlight.js');
      const highlight = new vm.SyntheticModule(['highlightAllIn'], function () {
        this.setExport('highlightAllIn', () => {});
      }, { context });
      await highlight.link(() => {}); await highlight.evaluate();
      return highlight;
    } });
    modules.set(name, mod);
    await mod.link(spec => spec === '../app.js' ? app : load(spec.replace('./', '')));
    return mod;
  }
  const competencies = await load('competencies.js');
  await competencies.evaluate();
  const page = await load('page.js');
  await page.evaluate();
  return { testing: modules.get('testing.js').namespace, competencies: competencies.namespace,
    page: page.namespace, modalRoot, calls, routes, location };
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

test('attempt shows named exclusions, warns about omissions and submits selected option IDs only', async () => {
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
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  assert.match(root.textContent, /Вопросы без ответа считаются неверными.*Повторная отправка невозможна/);
  all(submit, 'input')[0].checked = true;
  await fire(submit, 'submit');
  assert.equal(JSON.stringify(h.calls.at(-1).body), JSON.stringify({ answers: { [uuid]: [cid] } }));
  assert.deepEqual(h.routes, ['#/testing']);
});

test('manager assignments use named member/published test selects and timezone deadline', async () => {
  const h = await harness((_method, url) => url.endsWith('/members') ? [{ id: uuid, display_name: 'Анна Иванова', login: 'anna' }] :
    url.endsWith('/tests') ? [{ id: cid, title: 'Аналитика', version: 1, latest_published_version: 1, status: 'published', assignable: true },
      { id: did, title: 'Черновой тест', status: 'draft', assignable: false }] : [], { roles: ['manager'] });
  const root = await h.testing.renderManagerTesting({ id: did }, async () => {});
  assert.match(root.textContent, /Анна Иванова/);
  assert.ok(!root.textContent.includes('Черновой тест'));
  assert.ok(!root.textContent.includes(uuid));
  const assign = form(root, 'Назначить');
  const selects = all(assign, 'select');
  selects[0].value = uuid; selects[1].value = cid;
  assert.equal(all(assign, 'input')[0].attrs.required, '');
  for (const invalid of ['', 'bad', '2000-01-01T12:00']) {
    all(assign, 'input')[0].value = invalid;
    await fire(assign, 'submit');
    assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
    assert.match(root.textContent, /Укажите срок/);
  }
  all(assign, 'input')[0].value = '2099-01-01T12:00';
  await fire(assign, 'submit');
  const payload = h.calls.at(-1).body;
  assert.equal(payload.user_id, uuid); assert.equal(payload.test_id, cid);
  assert.match(payload.due_at, /Z$/);
  assert.equal(payload.due_at, new Date('2099-01-01T12:00').toISOString());
});

test('all-unanswered attempt is sent only by explicit submit', async () => {
  const h = await harness(method => method === 'GET' ? { status: 'in_progress', competencies: [],
    questions: [{ id: uuid, type: 'single', body: 'Question', options: [{ id: cid, body: 'Answer' }] }] } : {},
    { hash: `#/testing?attempt=${uuid}` });
  const root = await h.testing.renderTesting();
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  await fire(form(root, 'Отправить ответы'), 'submit');
  assert.equal(JSON.stringify(h.calls.at(-1).body), JSON.stringify({ answers: { [uuid]: [] } }));
  assert.deepEqual(h.routes, ['#/testing']);
});

test('legacy manager assignment without due hides start but in-progress can continue', async () => {
  const h = await harness(() => { throw new Error('No automatic requests'); });
  const root = h.testing.resultCards([{ assignment_id: uuid, status: 'assigned', assignment_type: 'manager_assigned', due_at: null }], true);
  assert.equal(button(root, 'Начать тест'), undefined);
  assert.match(root.textContent, /Срок назначения не указан.*Обратитесь к руководителю/);
  const progress = h.testing.resultCards([{ assignment_id: uuid, status: 'in_progress', assignment_type: 'manager_assigned', due_at: null, attempt_id: cid }], true);
  assert.equal(all(progress, 'a')[0].attrs.href, `#/testing?attempt=${cid}`);
  assert.equal(h.calls.length, 0);
});

test('due_missing conflict has an understandable message without automatic retry', async () => {
  const h = await harness(() => { throw Object.assign(new Error('Assignment deadline missing; contact your manager'), { status: 409, code: 'due_missing' }); });
  const root = h.testing.resultCards([{ assignment_id: uuid, status: 'assigned', assignment_type: 'manager_assigned', due_at: '2090-01-01T00:00:00Z' }], true);
  await fire(form(root, 'Начать тест'), 'submit');
  assert.match(root.textContent, /Срок назначения не указан.*Обратитесь к руководителю/);
  assert.doesNotMatch(root.textContent, /Данные изменились/);
  assert.equal(h.calls.length, 1);
});

test('wiki history requires an additive allowed role and restore requires server write ACL', async () => {
  for (const roles of [['employee'], ['employee', 'kk_responsible'], ...['admin', 'moderator', 'manager', 'eol', 'EOL', 'god'].map(role => ['employee', 'kk_responsible', role])]) {
    for (const canEdit of [false, true]) {
      const h = await harness((_method, url) => url.endsWith('/history') ? [{ id: cid, version: 1, created_at: '2026-01-01T00:00:00Z' }] :
        { id: uuid, created_by: uuid, title: 'Wiki', status: 'published', can_edit: canEdit, content: 'Text' }, { roles });
      const root = await h.page.renderPage(uuid);
      const history = button(root, '🕘 История');
      const allowed = roles.length === 3;
      assert.equal(Boolean(history), allowed);
      assert.equal(h.calls.length, 1);
      if (allowed) {
        await fire(history, 'click');
        assert.equal(Boolean(button(h.modalRoot, 'Восстановить')), canEdit);
      }
      assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
    }
  }
});

test('KK uses active-map names, validates test selection, shows correctness and both moderation decisions', async () => {
  let created;
  const h = await harness((method, url, body) => {
    if (method === 'POST' && url === '/api/testing/tests') { created = body; return { id: did }; }
    if (method !== 'GET') return {};
    if (url.includes('/questions?')) return url.includes('status=approved') ? [] : [{ id: uuid, competency_id: cid, body: 'Вопрос', moderation_status: 'pending' }];
    if (url.endsWith(`/questions/${uuid}`)) return { options: [{ body: 'Правильно', is_correct: true }, { body: 'Неправильно', is_correct: false }] };
    if (url.endsWith('/draft')) return { ...created, id: did, expected_token: 'saved', next_version: 1 };
    return [{ id: did, title: 'Новый тест', status: 'draft', has_draft: true, competencies: [{ id: cid, name: 'Аналитика' }] }];
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
  all(create, 'input')[1].value = 'Основной блок';
  let selects = all(create, 'select');
  selects[0].value = cid; await fire(selects[0], 'change');
  selects = all(create, 'select');
  selects[1].value = did; await fire(selects[1], 'change');
  await fire(create, 'submit');
  assert.equal(JSON.stringify(created.blocks), JSON.stringify([{ title: 'Основной блок', competencies: [
    { competency_id: cid, question_ids: [] }, { competency_id: did, question_ids: [] }
  ] }]));
  await fire(button(create, 'Опубликовать'), 'click');
  assert.match(create.textContent, /минимум два утверждённых вопроса/);
  assert.equal(h.calls.some(c => c.url.endsWith('/publish')), false);
  await fire(form(root, 'Отклонить'), 'submit');
  assert.equal(h.calls.find(c => c.method === 'POST' && c.url.endsWith('/review')).body.decision, 'reject');
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
  const h = await harness(method => method === 'GET' ? [] : {}, { roles: ['kk_responsible'] });
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
  const payload = h.calls.find(c => c.method === 'POST' && c.url === '/api/testing/questions').body;
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

const activeCompetencies = [
  { competency_id: cid, name: 'Аналитика' }, { competency_id: did, name: 'Общение' },
  { competency_id: 'comp-three', name: 'Планирование' }, { competency_id: 'comp-four', name: 'Исследования' }
];
const questionSet = competencyId => [1, 2, 3].map(n => ({ id: `${competencyId}-q${n}`, body: `Вопрос ${n}`, moderation_status: 'approved' }));
const draftFixture = (overrides = {}) => ({ id: uuid, title: 'Проверка навыков', description: '', status: 'draft',
  expected_token: 'token-one', next_version: 1, latest_published_version: null, title_locked: false,
  blocks: [{ title: 'Первый блок', competencies: activeCompetencies.slice(0, 2).map(c => ({
    competency_id: c.competency_id, question_ids: questionSet(c.competency_id).slice(0, 2).map(q => q.id)
  })) }], ...overrides });
const questionReply = url => questionSet(new URL(url, 'https://test.invalid').searchParams.get('competency_id'));
const selectCompetency = async (root, index, id) => {
  const node = all(root, 'select')[index]; node.value = id; await fire(node, 'change');
};
const hasClass = (node, className) => (node.attrs.class || '').split(' ').includes(className);
const competencyRows = root => all(root, 'fieldset').filter(n => hasClass(n, 'cm-test-competency'));

test('three roles: only KK edits drafts; manager and KK preview safe published versions, employee cannot', async () => {
  for (const role of ['employee', 'manager', 'kk_responsible']) {
    const h = await harness((_method, url) => {
      if (url.endsWith('/versions')) return [{ version: 1, block_count: 1, competency_count: 2, question_count: 2 }];
      if (url.endsWith('/versions/1')) return { test_title: 'Снимок теста', test_version: 1, blocks: [
        { title: 'Пустой блок', competencies: [] },
        { title: 'Опубликованный блок', competencies: [{ competency_name: 'Аналитика', questions: [
          { id: uuid, body: 'Вопрос снимка', type: 'single', options: [{ id: cid, body: 'Вариант снимка', is_correct: true }] }
        ] }] }
      ] };
      return [];
    }, { roles: [role] });
    const builder = await h.testing.renderTestBuilder(activeCompetencies);
    assert.equal(Boolean(button(builder, 'Создать тест')), role === 'kk_responsible');
    if (role !== 'kk_responsible') {
      assert.match(builder.textContent, /Черновики доступны только/);
      await h.testing.renderResponsibleTesting(activeCompetencies);
      assert.equal(h.calls.length, 0);
    }
    const preview = await h.testing.renderTestVersions(uuid);
    if (role === 'employee') {
      assert.equal(h.calls.length, 0);
      assert.equal(all(preview, 'form').length, 0);
      continue;
    }
    await fire(form(preview, 'Открыть версию 1'), 'submit');
    assert.match(preview.textContent, /Снимок теста.*Опубликованный блок.*Аналитика.*Вопрос снимка.*Вариант снимка/);
    assert.doesNotMatch(preview.textContent, /Пустой блок|Верный|Неверный|ключ/);
    assert.ok(!preview.textContent.includes(uuid));
    assert.ok(!preview.textContent.includes(cid));
    assert.equal(all(preview, 'input').length, 0);
    assert.ok(h.calls.every(c => c.method === 'GET' && !c.url.includes('/questions') && !c.url.endsWith('/draft')));
  }
});

test('builder creates explicit ordered blocks and questions, prevents duplicate competencies and removes blocks', async () => {
  let savedBody;
  const h = await harness((method, url, body) => {
    if (url.includes('/questions?')) return questionReply(url);
    if (method === 'POST') { savedBody = body; return { id: uuid }; }
    if (url.endsWith('/draft')) return { ...draftFixture(), ...savedBody };
    throw new Error(`Unexpected ${method} ${url}`);
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderTestBuilder(activeCompetencies);
  all(root, 'input')[0].value = 'Явный выбор';
  all(root, 'input')[1].value = 'Первый';
  await selectCompetency(root, 0, cid);
  // The selected competency is disabled in the other dropdown, and the event guard rejects duplicates.
  assert.equal(all(root, 'select')[1].children.find(n => n.value === cid).disabled, true);
  await selectCompetency(root, 1, cid);
  assert.equal(all(root, 'select')[1].value, '');
  await selectCompetency(root, 1, did);
  let row = competencyRows(root)[0];
  for (const n of [2, 0]) {
    const checkbox = all(row, 'input')[n]; checkbox.checked = true; await fire(checkbox, 'change');
  }
  await fire(button(row, 'Вопрос вниз'), 'click');
  await fire(button(root, 'Добавить блок'), 'click');
  const blocks = all(root, 'fieldset').filter(n => hasClass(n, 'cm-test-block'));
  all(blocks[1], 'input')[0].value = 'Второй';
  await selectCompetency(root, 2, 'comp-three');
  await selectCompetency(root, 3, 'comp-four');
  // Reorder competency rows as well as whole blocks.
  await fire(button(competencyRows(root)[2], 'Компетенция вниз'), 'click');
  await fire(button(all(root, 'fieldset').filter(n => hasClass(n, 'cm-test-block'))[0], 'Блок вниз'), 'click');
  await fire(root, 'submit');
  assert.equal(savedBody.title, 'Явный выбор');
  assert.equal(JSON.stringify(savedBody.blocks.map(b => b.title)), JSON.stringify(['Второй', 'Первый']));
  assert.equal(JSON.stringify(savedBody.blocks[0].competencies.map(c => c.competency_id)), JSON.stringify(['comp-four', 'comp-three']));
  assert.equal(JSON.stringify(savedBody.blocks[1].competencies[0].question_ids), JSON.stringify([`${cid}-q1`, `${cid}-q3`]));
  assert.deepEqual(h.calls.filter(c => c.url === '/api/testing/tests').map(c => c.method), ['POST']);
  assert.ok(h.calls.some(c => c.method === 'GET' && c.url.endsWith('/draft')));
  assert.ok(!root.textContent.includes(cid));
  const remainingBlocks = all(root, 'fieldset').filter(n => hasClass(n, 'cm-test-block'));
  await fire(button(remainingBlocks[0], 'Удалить блок'), 'click');
  assert.equal(all(root, 'fieldset').filter(n => hasClass(n, 'cm-test-block')).length, 1);
  assert.equal(button(root, 'Удалить блок').disabled, true);
});

test('draft save uses token, publish rejects unsaved edits and then uses the latest saved token', async () => {
  const h = await harness((method, url, body) => {
    if (url.includes('/questions?')) return questionReply(url);
    if (method === 'PUT') return { ...draftFixture(), ...body, expected_token: 'token-two' };
    if (url.endsWith('/publish')) return { id: uuid, version: 1 };
    throw new Error(`Unexpected ${method} ${url}`);
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderTestBuilder(activeCompetencies, draftFixture());
  all(root, 'input')[0].value = 'Новое название';
  await fire(button(root, 'Опубликовать'), 'click');
  assert.match(root.textContent, /Сначала сохраните изменения/);
  assert.equal(h.calls.some(c => c.url.endsWith('/publish')), false);
  await fire(root, 'submit');
  const save = h.calls.find(c => c.method === 'PUT');
  assert.equal(save.body.expected_token, 'token-one');
  assert.equal(save.body.title, 'Новое название');
  assert.equal(h.calls.some(c => c.url.endsWith('/publish')), false);
  await fire(button(root, 'Опубликовать'), 'click');
  assert.equal(h.calls.at(-1).url, `/api/testing/tests/${uuid}/publish`);
  assert.equal(h.calls.at(-1).body.expected_token, 'token-two');
  assert.equal(button(root, 'Сохранить черновик').disabled, true);
});

test('409 and 428 lock editor mutations until an explicit refresh, without automatic retry', async () => {
  for (const status of [409, 428]) {
    let refreshed = false;
    const h = await harness((method, url, body) => {
      if (url.includes('/questions?')) return questionReply(url);
      if (method === 'PUT') {
        if (!refreshed) throw Object.assign(new Error('stale'), { status });
        return { ...draftFixture(), ...body, expected_token: 'new-token' };
      }
      if (url.endsWith('/draft')) { refreshed = true; return draftFixture({ expected_token: 'fresh-token' }); }
      throw new Error(`Unexpected ${method} ${url}`);
    }, { roles: ['kk_responsible'] });
    const root = await h.testing.renderTestBuilder(activeCompetencies, draftFixture());
    await fire(root, 'submit');
    assert.match(root.textContent, status === 409 ? /Данные изменились/ : /Не хватает данных для проверки актуальности/);
    assert.equal(button(root, 'Сохранить черновик').disabled, true);
    const count = h.calls.length;
    await fire(root, 'submit'); await fire(button(root, 'Опубликовать'), 'click');
    assert.equal(h.calls.length, count);
    await fire(button(root, 'Обновить данные'), 'click');
    assert.equal(button(root, 'Сохранить черновик').disabled, false);
    assert.equal(h.calls.filter(c => c.method === 'PUT').length, 1);
    await fire(root, 'submit');
    assert.equal(h.calls.filter(c => c.method === 'PUT').at(-1).body.expected_token, 'fresh-token');
  }
});

test('approved question loading paginates per competency beyond 200 and keeps unavailable selections explicit', async () => {
  const oldId = '41111111-2222-4333-8444-555555555555';
  const questions = Array.from({ length: 205 }, (_, n) => ({ id: `q-${n}`, body: `Доступный вопрос ${n}` }));
  const initial = draftFixture();
  initial.blocks[0].competencies[0].question_ids = ['q-204', oldId];
  const h = await harness((_method, url) => {
    if (url.includes('/questions?')) {
      const params = new URL(url, 'https://test.invalid').searchParams;
      assert.equal(params.get('status'), 'approved');
      assert.equal(params.get('limit'), '100');
      if (params.get('competency_id') !== cid) return questionReply(url);
      const offset = Number(params.get('offset')); return questions.slice(offset, offset + 100);
    }
    if (url.endsWith(`/questions/${oldId}`)) return { body: 'Старый снятый вопрос', options: [{ body: 'Ключ не показывать', is_correct: true }] };
    return {};
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderTestBuilder(activeCompetencies, initial);
  assert.match(root.textContent, /Доступный вопрос 204.*Старый снятый вопрос.*Недоступен для публикации/s);
  assert.doesNotMatch(root.textContent, /Ключ не показывать/);
  assert.ok(!root.textContent.includes(oldId));
  assert.equal(h.calls.filter(c => c.url.includes(`competency_id=${cid}`)).length, 3);
  await fire(button(root, 'Опубликовать'), 'click');
  assert.match(root.textContent, /Уберите недоступные вопросы/);
  assert.equal(h.calls.some(c => c.method === 'POST'), false);
  const row = competencyRows(root)[0];
  await fire(all(row, 'button').filter(b => b.textContent === 'Убрать вопрос')[1], 'click');
  assert.doesNotMatch(row.textContent, /Старый снятый вопрос/);
});

test('inactive selected competency stays named generically and visible, never silently dropped', async () => {
  const initial = draftFixture();
  const h = await harness((_method, url) => url.includes('/questions?') ? questionReply(url) : {}, { roles: ['kk_responsible'] });
  const root = await h.testing.renderTestBuilder(activeCompetencies.slice(0, 1), initial);
  assert.match(root.textContent, /Ранее выбранная компетенция вне активной карты/);
  assert.equal(all(root, 'select')[1].value, did);
  assert.ok(!root.textContent.includes(did));
  await fire(button(root, 'Опубликовать'), 'click');
  assert.match(root.textContent, /замените компетенции вне активной карты/);
  assert.equal(h.calls.some(c => c.method === 'POST'), false);
});

test('builder displays hard block, competency and question limits instead of silently truncating', async () => {
  const questions = Array.from({ length: 51 }, (_, n) => ({ id: `choice-${n}`, body: `Вопрос ${n}` }));
  const h = await harness((_method, url) => url.includes('/questions?') ? questions : {}, { roles: ['kk_responsible'] });
  const root = await h.testing.renderTestBuilder(activeCompetencies);
  for (let n = 0; n < 9; n++) await fire(button(root, 'Добавить блок'), 'click');
  assert.match(root.textContent, /Блоков: 10\/10 · Компетенций: 20\/20/);
  await fire(button(root, 'Добавить блок'), 'click');
  assert.match(root.textContent, /В тесте может быть от 1 до 10 блоков/);
  await fire(button(root, 'Добавить компетенцию'), 'click');
  assert.match(root.textContent, /Не более 20 компетенций/);
  assert.equal(competencyRows(root).length, 20);
  await selectCompetency(root, 0, cid);
  const row = competencyRows(root)[0];
  for (let n = 0; n < 51; n++) {
    const checkbox = all(row, 'input')[n]; checkbox.checked = true; await fire(checkbox, 'change');
  }
  assert.match(row.textContent, /Выбрано вопросов: 50\/50/);
  assert.match(root.textContent, /Не более 50 вопросов на компетенцию/);
  assert.equal(all(row, 'input').filter(n => n.checked).length, 50);
});

test('published v1 and editable draft v2 coexist; first publication locks title and archive sends current token', async () => {
  const initial = draftFixture({ latest_published_version: 1, next_version: 2, title_locked: true });
  const h = await harness((method, url) => {
    if (url === '/api/testing/tests') return [{ id: uuid, title: 'Проверка навыков', status: 'published', version: 1,
      latest_published_version: 1, next_version: 2, has_draft: true, assignable: true, expected_token: 'list-token' }];
    if (url.includes('/questions?')) return url.includes('status=approved') ? questionReply(url) : [];
    if (url.endsWith('/draft')) return initial;
    if (method === 'POST') return {};
    throw new Error(`Unexpected ${method} ${url}`);
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderResponsibleTesting(activeCompetencies, async () => {});
  assert.match(root.textContent, /Опубликована версия 1 · Черновик версии 2/);
  await fire(form(root, 'Редактировать черновик'), 'submit');
  const editor = form(root, 'Сохранить черновик');
  assert.equal(all(editor, 'input')[0].disabled, true);
  assert.match(editor.textContent, /После первой публикации название теста изменить нельзя/);
  assert.equal(h.calls.some(c => c.method === 'POST'), false);
  await fire(form(root, 'Архивировать тест'), 'submit');
  assert.equal(h.calls.at(-1).body.expected_token, 'token-one');
});

test('begin new draft posts the list token and opens returned draft, without touching published version', async () => {
  const h = await harness((method, url) => {
    if (url === '/api/testing/tests') return [{ id: uuid, title: 'Проверка навыков', status: 'published', version: 1,
      latest_published_version: 1, has_draft: false, assignable: true, expected_token: 'list-token' }];
    if (url.includes('/questions?')) return url.includes('status=approved') ? questionReply(url) : [];
    if (method === 'POST' && url.endsWith('/draft')) return draftFixture({ latest_published_version: 1, next_version: 2, title_locked: true });
    throw new Error(`Unexpected ${method} ${url}`);
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderResponsibleTesting(activeCompetencies, async () => {});
  await fire(form(root, 'Создать новую версию'), 'submit');
  const posts = h.calls.filter(c => c.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, `/api/testing/tests/${uuid}/draft`);
  assert.equal(posts[0].body.expected_token, 'list-token');
  assert.ok(form(root, 'Сохранить черновик'));
  assert.match(root.textContent, /Опубликована версия 1 · Черновик версии 2/);
});

test('manager can assign published version while a draft exists, chooses an explicit version or latest-at-assignment', async () => {
  const h = await harness((_method, url) => {
    if (url.endsWith('/members')) return [{ id: did, display_name: 'Сотрудник' }];
    if (url.endsWith('/tests')) return [
      { id: uuid, title: 'Тест с черновиком', status: 'draft', has_draft: true, assignable: true, latest_published_version: 2 },
      { id: cid, title: 'Недоступный тест', status: 'published', assignable: false, latest_published_version: 1 }
    ];
    if (url.endsWith('/versions')) return [{ version: 2 }, { version: 1 }];
    return {};
  }, { roles: ['manager'] });
  const root = await h.testing.renderManagerTesting({ id: cid }, async () => {});
  assert.match(root.textContent, /Тест с черновиком/);
  assert.doesNotMatch(root.textContent, /Недоступный тест|Черновик версии/);
  const assign = form(root, 'Назначить');
  const [employee, selectedTest, version] = all(assign, 'select');
  employee.value = did; selectedTest.value = uuid;
  await fire(selectedTest, 'change');
  assert.match(version.textContent, /Версия 2.*Версия 1/);
  version.value = '1'; all(assign, 'input')[0].value = '2099-01-01T12:00';
  await fire(assign, 'submit');
  assert.equal(h.calls.at(-1).body.test_version, 1);
  assert.equal(h.calls.at(-1).body.test_id, uuid);
  version.value = '';
  await fire(assign, 'submit');
  assert.equal(Object.hasOwn(h.calls.at(-1).body, 'test_version'), false);
  assert.ok(!root.textContent.includes(uuid));
  assert.equal(h.calls.some(c => c.url.endsWith('/draft')), false);
});

test('block attempts render ordered named headings, skip empty blocks and allow unanswered submission; completed hides questions', async () => {
  const view = { test_title: 'Замороженный тест', test_version: 2, status: 'in_progress',
    competencies: [{ competency_name: 'Общение', state: 'excluded', exclusion_reason: 'cooldown_30d', available_at: '2090-01-01T12:00:00Z' }],
    questions: [{ id: 'legacy-should-not-render', body: 'Лишний плоский вопрос', options: [] }],
    blocks: [
      { title: 'Пустой блок', competencies: [{ competency_name: 'Пустая компетенция', questions: [] }] },
      { title: 'Включённый блок', competencies: [{ competency_name: 'Аналитика', questions: [
        { id: uuid, type: 'multiple', body: 'Вопрос блока', options: [{ id: cid, body: 'Ответ блока', is_correct: true }] }
      ] }] }
    ] };
  const h = await harness(method => method === 'GET' ? view : {}, { hash: `#/testing?attempt=${uuid}` });
  let root = await h.testing.renderTesting();
  assert.match(root.textContent, /Замороженный тест.*Версия 2.*Общение.*Исключена: период ожидания.*Включённый блок.*Аналитика.*Вопрос блока/s);
  assert.doesNotMatch(root.textContent, /Пустой блок|Пустая компетенция|Лишний плоский вопрос|Верный/);
  assert.ok(!root.textContent.includes(uuid));
  await fire(form(root, 'Отправить ответы'), 'submit');
  assert.equal(JSON.stringify(h.calls.at(-1).body), JSON.stringify({ answers: { [uuid]: [] } }));
  view.status = 'completed'; h.location.hash = `#/testing?attempt=${uuid}`;
  root = await h.testing.renderTesting();
  assert.doesNotMatch(root.textContent, /Вопрос блока|Ответ блока|Включённый блок/);
  assert.equal(all(root, 'input').length, 0);
  const result = h.testing.resultCards([{ assignment_id: uuid, test_title: 'Результат', test_version: 2, status: 'completed' }]);
  assert.match(result.textContent, /Версия 2/);
});

test('bank remains filterable and paginated beyond the first page', async () => {
  const h = await harness((_method, url) => {
    if (url === '/api/testing/tests') return [];
    if (url.includes('/questions?')) {
      const query = new URL(url, 'https://test.invalid').searchParams;
      if (query.get('competency_id')) return [{ id: uuid, body: 'Отфильтрованный вопрос', moderation_status: 'approved' }];
      if (query.get('offset') === '0') return Array.from({ length: 100 }, (_, n) => ({ id: `q-${n}`, body: `Банк ${n}`, moderation_status: 'approved' }));
      return [{ id: uuid, body: 'Вторая страница', moderation_status: 'approved' }];
    }
    return {};
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderResponsibleTesting(activeCompetencies);
  assert.equal(button(root, 'Предыдущая страница вопросов').disabled, true);
  await fire(button(root, 'Следующая страница вопросов'), 'click');
  assert.match(root.textContent, /Вторая страница/);
  assert.match(h.calls.at(-1).url, /offset=100/);
  assert.equal(button(root, 'Следующая страница вопросов').disabled, true);
  const filter = form(root, 'Найти вопросы');
  all(filter, 'select')[0].value = cid; all(filter, 'select')[1].value = 'approved';
  await fire(filter, 'submit');
  assert.match(h.calls.at(-1).url, /offset=0.*competency_id=.*status=approved/);
  assert.match(root.textContent, /Отфильтрованный вопрос/);
  assert.doesNotMatch(root.textContent, /Вторая страница/);
});

test('dirty test builders block archive before POST; explicit discard archives once', async () => {
  let refreshes = 0;
  const h = await harness((method, url) => {
    if (url === '/api/testing/tests') return [{ id: uuid, title: 'Тест', status: 'draft', expected_token: 'list-v1' }];
    if (url.includes('/questions?') || url.includes('/imports?')) return [];
    if (method === 'POST' && url.endsWith('/archive')) return { id: uuid };
    throw Error(`Unexpected ${method} ${url}`);
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderResponsibleTesting(activeCompetencies, async () => { refreshes++; });
  const builder = form(root, 'Создать тест');
  all(builder, 'input')[0].value = 'Несохранённый тест';
  await fire(form(root, 'Архивировать тест'), 'submit');
  assert.equal(h.calls.filter(call => call.method === 'POST').length, 0);
  assert.equal(refreshes, 0);
  assert.equal(form(root, 'Создать тест'), builder);
  assert.match(root.textContent, /Архивация отменена.*несохранённые изменения/);
  await fire(form(root, 'Сбросить правки и архивировать'), 'submit');
  assert.equal(h.calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(h.calls.at(-1).body.expected_token, 'list-v1');
  assert.equal(refreshes, 1);
});

test('dirty responsible tab blocks role switching and direct tab refresh until explicit discard', async () => {
  const h = await harness((method, url) => {
    if (url === '/api/competencies/departments') return [{ id: did, name: 'Отдел', responsible: true }];
    if (url === '/api/competencies/catalog') return { categories: [], competencies: [] };
    if (url.endsWith('/profiles') || url === '/api/testing/tests' || url.includes('/questions?') || url.includes('/imports?')) return [];
    if (method === 'POST' && url.endsWith('/profiles')) return { id: cid };
    if (url === '/api/competencies/mine' || url === '/api/testing/results') return [];
    throw Error(`Unexpected ${method} ${url}`);
  }, { roles: ['kk_responsible', 'employee'], hash: '#/competencies?tab=responsible' });
  const root = await h.competencies.renderCompetencies();
  const builder = form(root, 'Создать тест');
  const title = all(builder, 'input')[0]; title.value = 'Несохранённый';
  const employee = all(root, 'button').find(n => n.attrs.role === 'tab' && n.textContent === 'Мои компетенции');
  await fire(employee, 'click');
  assert.equal(h.location.hash, '#/competencies?tab=responsible');
  assert.equal(form(root, 'Создать тест'), builder);
  await fire(button(root, 'Остаться'), 'click');
  assert.equal(title.value, 'Несохранённый');
  await fire(form(root, 'Создать проект'), 'submit');
  assert.equal(form(root, 'Создать тест'), builder);
  assert.equal(h.location.hash, '#/competencies?tab=responsible');
  await fire(employee, 'click');
  await fire(button(root, 'Сбросить правки и продолжить'), 'click');
  assert.equal(h.location.hash, '#/competencies?tab=employee');
  assert.equal(form(root, 'Создать тест'), undefined);
});

test('inactive approved questions remain selected with a reason but cannot be published', async () => {
  const h = await harness((_method, url) => {
    if (url.includes('/questions?')) return questionReply(url).map((q, n) => ({ ...q, is_active: n !== 0 }));
    return { body: 'Неактивный вопрос' };
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderTestBuilder(activeCompetencies, draftFixture());
  assert.match(root.textContent, /Неактивный вопрос.*Недоступен для публикации/s);
  assert.match(root.textContent, /Выбрано вопросов: 2\/50/);
  await fire(button(root, 'Опубликовать'), 'click');
  assert.equal(h.calls.some(c => c.method === 'POST'), false);
  assert.match(root.textContent, /Уберите недоступные вопросы/);
});

test('failed read after create never creates twice; explicit refresh is retryable and obtains a token', async () => {
  let created, reads = 0;
  const h = await harness((method, url, body) => {
    if (url.includes('/questions?')) return [];
    if (method === 'POST') { created = body; return { id: uuid }; }
    if (url.endsWith('/draft')) {
      if (++reads <= 2) throw new Error('Временная ошибка чтения');
      return { ...draftFixture(), ...created };
    }
    return {};
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderTestBuilder(activeCompetencies);
  all(root, 'input')[0].value = 'Тест'; all(root, 'input')[1].value = 'Блок';
  await selectCompetency(root, 0, cid); await selectCompetency(root, 1, did);
  await fire(root, 'submit');
  assert.equal(button(root, 'Создать тест').disabled, true);
  await fire(root, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
  await fire(button(root, 'Обновить данные'), 'click');
  assert.ok(button(root, 'Обновить данные'));
  await fire(button(root, 'Обновить данные'), 'click');
  assert.equal(button(root, 'Сохранить черновик').disabled, false);
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('successful publication remains locked even if refreshing its list fails', async () => {
  const h = await harness((_method, url) => url.includes('/questions?') ? questionReply(url) : { id: uuid, version: 1 }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderTestBuilder(activeCompetencies, draftFixture(), async () => { throw new Error('Network'); });
  await fire(button(root, 'Опубликовать'), 'click');
  assert.match(root.textContent, /Версия 1 опубликована.*обновите страницу/);
  assert.equal(button(root, 'Опубликовать').disabled, true);
  await fire(button(root, 'Опубликовать'), 'click');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('an explicitly empty blocks array never falls back to legacy flat questions', async () => {
  const h = await harness(() => ({ status: 'in_progress', competencies: [], blocks: [],
    questions: [{ id: uuid, body: 'Не показывать плоский вопрос', type: 'single', options: [] }] }), { hash: `#/testing?attempt=${uuid}` });
  const root = await h.testing.renderTesting();
  assert.doesNotMatch(root.textContent, /Не показывать плоский вопрос/);
  assert.equal(all(root, 'fieldset').length, 0);
});

// This refresh really replaces the mounted tab, matching competencies.js, rather than a noop.
async function mountResponsible(h) {
  const host = el('div');
  let refreshes = 0;
  const refresh = async () => {
    refreshes++;
    host.replaceChildren(await h.testing.renderResponsibleTesting(activeCompetencies, refresh));
  };
  host.append(await h.testing.renderResponsibleTesting(activeCompetencies, refresh));
  return { host, get refreshes() { return refreshes; }, get root() { return host.children[0]; } };
}

test('opening an existing or new draft keeps the editor mounted instead of calling tab refresh', async () => {
  for (const hasDraft of [true, false]) {
    const h = await harness((_method, url) => {
      if (url === '/api/testing/tests') return [{ id: uuid, title: 'Тест', status: 'published', latest_published_version: 1,
        has_draft: hasDraft, expected_token: 'list-token' }];
      if (url.endsWith('/draft')) return draftFixture({ latest_published_version: 1, next_version: 2, title_locked: true });
      if (url.includes('/questions?')) return url.includes('status=approved') ? questionReply(url) : [];
      throw new Error(`Unexpected ${url}`);
    }, { roles: ['kk_responsible'] });
    const mounted = await mountResponsible(h);
    const root = mounted.root;
    const open = form(root, hasDraft ? 'Редактировать черновик' : 'Создать новую версию');
    await fire(open, 'submit');
    assert.equal(mounted.refreshes, 0);
    assert.equal(mounted.root, root);
    const editor = form(mounted.root, 'Сохранить черновик');
    assert.ok(editor);
    all(editor, 'textarea')[0].value = 'Несохранённое описание';
    await fire(open, 'submit');
    assert.equal(form(mounted.root, 'Сохранить черновик'), editor);
    assert.equal(all(editor, 'textarea')[0].value, 'Несохранённое описание');
    assert.equal(h.calls.filter(c => c.url.endsWith('/draft')).length, 1);
    assert.equal(mounted.refreshes, 0);
  }
});

test('draft-open conflict retains explicit real tab refresh and never retries the mutation automatically', async () => {
  const h = await harness((_method, url) => {
    if (url === '/api/testing/tests') return [{ id: uuid, title: 'Тест', status: 'published', latest_published_version: 1,
      has_draft: false, expected_token: 'list-token' }];
    if (url.endsWith('/draft')) throw Object.assign(new Error('stale'), { status: 409 });
    if (url.includes('/questions?')) return [];
    throw new Error(`Unexpected ${url}`);
  }, { roles: ['kk_responsible'] });
  const mounted = await mountResponsible(h);
  const root = mounted.root;
  const open = form(root, 'Создать новую версию');
  await fire(open, 'submit');
  assert.equal(mounted.refreshes, 0);
  assert.match(open.textContent, /Данные изменились/);
  await fire(button(open, 'Обновить данные'), 'click');
  assert.equal(mounted.refreshes, 1);
  assert.notEqual(mounted.root, root);
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('bank create, approve, reject and filtering preserve dirty new and existing editors while exposing newly approved questions', async () => {
  const retiredId = 'retired-selected';
  const pendingId = 'pending-new';
  const createdId = 'created-question';
  const initial = draftFixture({ latest_published_version: 1, next_version: 2, title_locked: true });
  initial.blocks[0].competencies[0].question_ids.push(retiredId);
  const bank = [{ id: pendingId, competency_id: cid, body: 'Вопрос на утверждение', moderation_status: 'pending' }];
  const h = await harness((method, url, body) => {
    if (url === '/api/testing/tests') return [{ id: uuid, title: 'Тест', status: 'published', latest_published_version: 1,
      has_draft: true, expected_token: 'list-token' }];
    if (url.endsWith('/draft')) return method === 'PUT' ? { ...initial, ...body, expected_token: 'saved-token' } : initial;
    if (url === '/api/testing/questions' && method === 'POST') {
      bank.push({ id: createdId, competency_id: body.competency_id, body: body.body, moderation_status: 'pending' });
      return { id: createdId };
    }
    if (url.endsWith('/review')) {
      const q = bank.find(q => url.endsWith(`/questions/${q.id}/review`));
      q.moderation_status = body.decision === 'approve' ? 'approved' : 'rejected';
      return {};
    }
    if (url.endsWith(`/questions/${retiredId}`)) return { body: 'Ранее выбранный снятый вопрос' };
    if (url.includes('/questions?')) {
      const params = new URL(url, 'https://test.invalid').searchParams;
      if (params.get('competency_id') && params.get('status') === 'approved') return [
        ...questionReply(url), ...bank.filter(q => q.competency_id === params.get('competency_id') && q.moderation_status === 'approved')
      ];
      return bank.map(q => ({ ...q }));
    }
    throw new Error(`Unexpected ${method} ${url}`);
  }, { roles: ['kk_responsible'] });
  const mounted = await mountResponsible(h);
  const root = mounted.root;
  const newEditor = form(root, 'Создать тест');
  all(newEditor, 'input')[0].value = 'Несохранённый новый тест';
  all(newEditor, 'input')[1].value = 'Несохранённый новый блок';
  all(newEditor, 'textarea')[0].value = 'Описание нового теста';
  await selectCompetency(newEditor, 0, cid); await selectCompetency(newEditor, 1, did);
  for (const qid of [`${cid}-q2`, `${cid}-q1`]) {
    const checkbox = all(competencyRows(newEditor)[0], 'input').find(n => n.value === qid);
    checkbox.checked = true; await fire(checkbox, 'change');
  }
  await fire(button(newEditor, 'Добавить блок'), 'click');
  const extraBlock = all(newEditor, 'fieldset').filter(n => hasClass(n, 'cm-test-block'))[1];
  all(extraBlock, 'input')[0].value = 'Дополнительный блок';
  await selectCompetency(newEditor, 2, 'comp-three'); await selectCompetency(newEditor, 3, 'comp-four');
  await fire(form(root, 'Редактировать черновик'), 'submit');
  const editor = form(root, 'Сохранить черновик');
  all(editor, 'input')[1].value = 'Изменённый существующий блок';
  all(editor, 'textarea')[0].value = 'Несохранённое описание черновика';
  const assertPreserved = () => {
    assert.equal(mounted.refreshes, 0);
    assert.equal(mounted.root, root);
    assert.equal(form(root, 'Создать тест'), newEditor);
    assert.equal(form(root, 'Сохранить черновик'), editor);
    assert.equal(all(newEditor, 'input')[0].value, 'Несохранённый новый тест');
    assert.equal(all(newEditor, 'input')[1].value, 'Несохранённый новый блок');
    assert.equal(all(newEditor, 'textarea')[0].value, 'Описание нового теста');
    assert.equal(all(extraBlock, 'input')[0].value, 'Дополнительный блок');
    assert.equal(competencyRows(newEditor).length, 4);
    assert.equal(all(editor, 'input')[1].value, 'Изменённый существующий блок');
    assert.equal(all(editor, 'textarea')[0].value, 'Несохранённое описание черновика');
    assert.match(editor.textContent, /Ранее выбранный снятый вопрос.*Недоступен для публикации/s);
    const selected = all(newEditor, 'ol').find(n => hasClass(n, 'cm-selected-questions'));
    assert.deepEqual(selected.children.map(li => all(li, 'p')[0].textContent), ['Вопрос 2', 'Вопрос 1']);
    assert.equal(all(competencyRows(newEditor)[0], 'input').filter(n => n.checked).length, 2);
    assert.equal(h.calls.filter(c => c.method === 'GET' && c.url.endsWith('/draft')).length, 1);
  };
  const questionForm = form(root, 'Сохранить вопрос');
  all(questionForm, 'select')[0].value = cid;
  all(questionForm, 'textarea')[0].value = 'Новый вопрос из формы';
  const optionTexts = all(questionForm, 'input').filter(n => !n.attrs.type);
  optionTexts[0].value = 'Да'; optionTexts[1].value = 'Нет';
  all(questionForm, 'input').find(n => n.attrs.type === 'checkbox').checked = true;
  await fire(questionForm, 'submit');
  assertPreserved();
  const questionCard = text => all(root, 'section').find(n => all(n, 'h3').some(h => h.textContent === text));
  await fire(form(questionCard('Вопрос на утверждение'), 'Одобрить'), 'submit');
  assertPreserved();
  const newlyApproved = all(competencyRows(editor)[0], 'input').find(n => n.value === pendingId);
  assert.ok(newlyApproved);
  assert.equal(newlyApproved.checked, false); // Approval adds a choice, never implicitly changes the test.
  newlyApproved.checked = true; await fire(newlyApproved, 'change');
  await fire(form(questionCard('Новый вопрос из формы'), 'Отклонить'), 'submit');
  assertPreserved();
  assert.equal(all(competencyRows(editor)[0], 'input').find(n => n.value === pendingId).checked, true);
  const filter = form(root, 'Найти вопросы');
  all(filter, 'select')[0].value = cid; all(filter, 'select')[1].value = 'approved';
  await fire(filter, 'submit');
  assertPreserved();
  await fire(button(root, 'Обновить список тестов'), 'click');
  assert.match(root.textContent, /Сначала сохраните изменения всех открытых тестов/);
  assertPreserved();
  await fire(editor, 'submit');
  const saved = h.calls.find(c => c.method === 'PUT').body;
  assert.equal(saved.description, 'Несохранённое описание черновика');
  assert.equal(saved.blocks[0].title, 'Изменённый существующий блок');
  assert.equal(JSON.stringify(saved.blocks[0].competencies[0].question_ids), JSON.stringify([`${cid}-q1`, `${cid}-q2`, retiredId, pendingId]));
  assert.equal(mounted.refreshes, 0);
});

test('refreshing question choices keeps selected controls visible during loading and keeps dirty state on failure', async () => {
  const gate = deferred();
  let delayed = false;
  const h = await harness((_method, url) => {
    if (url.includes('/questions?')) return delayed ? gate.promise : questionReply(url);
    throw new Error(`Unexpected ${url}`);
  }, { roles: ['kk_responsible'] });
  const root = await h.testing.renderTestBuilder(activeCompetencies, draftFixture());
  all(root, 'textarea')[0].value = 'Изменения остаются';
  delayed = true;
  const loading = root.refreshQuestionChoices();
  assert.match(root.textContent, /Выбор и порядок сохранены/);
  assert.equal(all(competencyRows(root)[0], 'input').filter(n => n.checked).length, 2);
  assert.ok(all(competencyRows(root)[0], 'input').every(n => n.disabled));
  gate.reject(new Error('Ошибка загрузки банка'));
  await loading;
  assert.match(root.textContent, /Ошибка загрузки банка/);
  assert.equal(all(root, 'textarea')[0].value, 'Изменения остаются');
  assert.equal(root.hasUnsavedChanges(), true);
  assert.equal(all(competencyRows(root)[0], 'input').filter(n => n.checked).length, 2);
});

test('local question-create conflict refresh unlocks retry without replacing a dirty tab or automatically posting', async () => {
  const h = await harness((method, url) => {
    if (method === 'POST') throw Object.assign(new Error('stale'), { status: 409 });
    if (url === '/api/testing/tests' || url.includes('/questions?')) return [];
    throw new Error(`Unexpected ${url}`);
  }, { roles: ['kk_responsible'] });
  const mounted = await mountResponsible(h);
  const root = mounted.root;
  all(form(root, 'Создать тест'), 'input')[0].value = 'Не терять';
  const create = form(root, 'Сохранить вопрос');
  all(create, 'select')[0].value = cid;
  all(create, 'textarea')[0].value = 'Вопрос';
  all(create, 'input').filter(n => !n.attrs.type).forEach((n, i) => n.value = `Вариант ${i + 1}`);
  all(create, 'input').find(n => n.attrs.type === 'checkbox').checked = true;
  await fire(create, 'submit');
  assert.equal(button(create, 'Сохранить вопрос').disabled, true);
  await fire(button(create, 'Обновить данные'), 'click');
  assert.equal(button(create, 'Сохранить вопрос').disabled, false);
  assert.equal(mounted.refreshes, 0);
  assert.equal(mounted.root, root);
  assert.equal(all(form(root, 'Создать тест'), 'input')[0].value, 'Не терять');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('new-test conflict can explicitly refresh its warned-about editor without a dirty-guard deadlock', async () => {
  const h = await harness(method => {
    if (method === 'POST') throw Object.assign(new Error('stale'), { status: 409 });
    return [];
  }, { roles: ['kk_responsible'] });
  const mounted = await mountResponsible(h);
  const root = mounted.root;
  const editor = form(root, 'Создать тест');
  all(editor, 'input')[0].value = 'Новый тест'; all(editor, 'input')[1].value = 'Блок';
  await selectCompetency(editor, 0, cid); await selectCompetency(editor, 1, did);
  await fire(editor, 'submit');
  assert.match(editor.textContent, /Несохранённые изменения будут заменены/);
  assert.equal(mounted.refreshes, 0);
  await fire(button(editor, 'Обновить данные'), 'click');
  assert.equal(mounted.refreshes, 1);
  assert.notEqual(mounted.root, root);
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});
