// Run: node --experimental-vm-modules --test kb/tests/import.frontend.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { TextDecoder, TextEncoder } from 'node:util';
import vm from 'node:vm';

class Node {
  constructor(tag, attrs = {}) {
    this.tag = tag; this.attrs = attrs; this.children = []; this.events = {};
    this.value = attrs.value || ''; this.checked = false; this.disabled = false;
    for (const [name, callback] of Object.entries(attrs)) if (name.startsWith('on')) this.addEventListener(name.slice(2), callback);
  }
  append(...nodes) { for (const n of nodes.flat()) if (n != null) { this.children.push(n); if (n instanceof Node) n.parent = this; } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attrs[name] = value; }
  getAttribute(name) { return this.attrs[name] ?? null; }
  addEventListener(event, callback) { (this.events[event] ||= []).push(callback); }
  reportValidity() { return true; }
  get textContent() { return this.children.map(n => n instanceof Node ? n.textContent : String(n)).join(''); }
}
const el = (tag, attrs = {}, ...children) => { const n = new Node(tag, attrs); n.append(...children); return n; };
const all = (root, tag) => [root, ...root.children.filter(n => n instanceof Node).flatMap(n => all(n, tag))].filter(n => !tag || n.tag === tag);
const button = (root, text) => all(root, 'button').find(n => n.textContent === text);
const buttons = (root, text) => all(root, 'button').filter(n => n.textContent === text);
const form = (root, text) => all(root, 'form').find(n => button(n, text)?.attrs.type === 'submit');
async function fire(node, event) {
  assert.ok(node, `missing ${event} target`);
  for (let target = node; target; target = ['input', 'change'].includes(event) ? target.parent : null) {
    for (const fn of target.events[event] || []) await fn({ target: node, preventDefault() {} });
  }
}
const file = (name, text) => ({ name, size: new TextEncoder().encode(text).length,
  arrayBuffer: async () => new TextEncoder().encode(text) });

async function harness(respond, roles = ['kk_responsible']) {
  const calls = [], state = { user: { roles } };
  const context = vm.createContext({ URLSearchParams, TextDecoder, TextEncoder });
  const app = new vm.SyntheticModule(['api', 'el', 'navigate', 'state'], function () {
    this.setExport('el', el); this.setExport('state', state); this.setExport('navigate', () => {});
    this.setExport('api', async (method, url, body) => { calls.push({ method, url, body }); return respond(method, url, body); });
  }, { context });
  const source = await readFile(new URL('../static/js/views/testing.js', import.meta.url), 'utf8');
  const module = new vm.SourceTextModule(source, { context });
  await module.link(() => app); await module.evaluate();
  return { testing: module.namespace, calls };
}

test('only KK can access imports; employee and manager never request or see answers', async () => {
  for (const roles of [['employee'], ['manager'], ['manager', 'employee']]) {
    const h = await harness(() => { throw Error('privileged API invoked'); }, roles);
    const result = await h.testing.renderQuestionImports();
    assert.match(result.textContent, /только ответственному/);
    assert.equal(h.calls.length, 0);
    assert.equal(button(result, 'Загрузить JSON'), undefined);
  }
});

test('upload checks extension, bytes and UTF-8 JSON before a single POST; shows history and totals', async () => {
  const h = await harness((method, url) => url === '/api/testing/imports' ? { id: 'import-1' } :
    url.startsWith('/api/testing/imports?') ? [{ id: 'import-1', filename: 'sample.json', status: 'pending', total: 1, pending: 1, invalid: 0, approved: 0, rejected: 0 }] :
      { filename: 'sample.json', status: 'pending', total: 1, pending: 1, invalid: 0, approved: 0, rejected: 0, items: [] });
  const root = await h.testing.renderQuestionImports();
  const upload = form(root, 'Загрузить JSON'), picker = all(upload, 'input')[0];
  assert.match(root.textContent, /512 КиБ/);
  assert.match(picker.attrs.accept, /\.json/);
  for (const f of [file('wrong.txt', '{}'), { ...file('big.json', '{}'), size: 524289 }, file('broken.json', '{'),
    { name: 'bad-utf8.json', size: 2, arrayBuffer: async () => Uint8Array.of(0xc3, 0x28) }]) {
    picker.files = [f]; await fire(upload, 'submit');
    assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  }
  picker.files = [file('sample.json', '{"questions":[]}')]; await fire(picker, 'change');
  await fire(upload, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
  assert.equal(JSON.stringify(h.calls.find(c => c.method === 'POST').body), JSON.stringify({ filename: 'sample.json', json_text: '{"questions":[]}' }));
  assert.match(root.textContent, /Всего: 1.*На проверке: 1.*Записи: 0–0 из 1/);
  await fire(upload, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('invalid and pending rows: edit structured fields, save token, approve only after validation, reject with reason', async () => {
  const records = [
    { id: 'one', ordinal: 1, status: 'invalid', expected_token: 'v1', validation_errors: ['Нужен код компетенции'],
      payload: { question: 'Исходный вопрос', type: 'single', options: [{ id: 'a', text: 'Да', is_correct: true }, { id: 'b', text: 'Нет', is_correct: false }], source_page_id: 'page-1' } },
    { id: 'two', ordinal: 2, status: 'pending', expected_token: 'v1', validation_errors: [],
      payload: { question: 'Другая запись', competency_code: 'K2', type: 'single', options: [{ text: 'Да', is_correct: true }, { text: 'Нет', is_correct: false }] } }
  ];
  const h = await harness((method, url, body) => {
    if (method === 'GET' && url.startsWith('/api/testing/imports?')) return [{ id: 'batch', filename: 'batch.json', status: 'pending', total: 2, pending: 1, invalid: 1, approved: 0, rejected: 0 }];
    if (method === 'GET') return { id: 'batch', filename: 'batch.json', status: 'pending', total: 2, pending: 1, invalid: 1, approved: 0, rejected: 0, items: records };
    const row = records.find(r => url.includes(`/items/${r.id}`));
    if (method === 'PUT') { Object.assign(row, { status: 'pending', validation_errors: [], edited_payload: body.edited_payload, expected_token: 'v2' }); return { ...row }; }
    if (body.decision === 'approve') { Object.assign(row, { status: 'approved', expected_token: 'v3' }); return { ...row }; }
    Object.assign(row, { status: 'rejected', rejection_reason: body.reason }); return { ...row };
  });
  const root = await h.testing.renderQuestionImports();
  await fire(button(root, 'Открыть импорт'), 'click');
  assert.match(root.textContent, /Ошибки проверки.*Нужен код.*Исходная запись.*Сохранённые правки/s);
  let edit = form(root, 'Сохранить правки');
  assert.equal(button(edit, 'Одобрить запись').disabled, true);
  const inputs = all(edit, 'input');
  const code = inputs.find(n => n.attrs.maxlength === 200 && n.attrs.type !== 'checkbox');
  code.value = 'K1';
  all(edit, 'textarea')[0].value = 'Исправленный вопрос';
  const answer = all(edit, 'input').find(n => n.attrs.type === 'checkbox' && n.checked);
  answer.checked = false; await fire(answer, 'change');
  const other = all(edit, 'input').filter(n => n.attrs.type === 'checkbox')[1]; other.checked = true; await fire(other, 'change');
  await fire(edit, 'submit');
  const update = h.calls.find(c => c.method === 'PUT');
  assert.equal(update.body.expected_token, 'v1');
  assert.equal(update.body.edited_payload.competency_code, 'K1');
  assert.equal(update.body.edited_payload.question, 'Исправленный вопрос');
  assert.equal(update.body.edited_payload.options[1].is_correct, true);
  assert.match(root.textContent, /Сохранённые правки.*Исправленный вопрос/s);
  edit = form(root, 'Сохранить правки');
  assert.equal(button(edit, 'Одобрить запись').disabled, false);
  await fire(button(edit, 'Одобрить запись'), 'click');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  assert.match(edit.textContent, /Подтвердите источник/);
  const confirmed = all(edit, 'input').filter(n => n.attrs.type === 'checkbox').at(-1);
  confirmed.checked = true; await fire(button(edit, 'Одобрить запись'), 'click');
  const approval = h.calls.find(c => c.method === 'POST');
  assert.equal(JSON.stringify(approval.body), JSON.stringify({ expected_token: 'v2', decision: 'approve', source_confirmed: true }));
  assert.equal(all(root, 'form').filter(n => button(n, 'Одобрить запись')).length, 1);
  const second = form(root, 'Сохранить правки');
  await fire(button(second, 'Отклонить запись'), 'click');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
  all(second, 'textarea').at(-1).value = 'Не соответствует статье';
  await fire(button(second, 'Отклонить запись'), 'click');
  assert.equal(h.calls.filter(c => c.method === 'POST').at(-1).body.reason, 'Не соответствует статье');
  assert.match(root.textContent, /Причина отклонения: Не соответствует статье/);
  assert.equal(all(root, 'form').filter(n => button(n, 'Сохранить правки')).length, 0);
});

test('file change during pending upload cannot trigger a second POST and refresh failure remains visible', async () => {
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  let detailFails = true;
  const h = await harness((method, url) => {
    if (method === 'POST') return gate;
    if (url.startsWith('/api/testing/imports?')) return [];
    if (detailFails) throw new Error('Сеть недоступна');
    return { filename: 'first.json', status: 'validated', total: 0, items: [] };
  });
  const root = await h.testing.renderQuestionImports();
  const upload = form(root, 'Загрузить JSON'), picker = all(upload, 'input')[0];
  picker.files = [file('first.json', '[]')];
  const pending = fire(upload, 'submit');
  while (!h.calls.some(call => call.method === 'POST')) await Promise.resolve();
  picker.files = [file('second.json', '[]')]; await fire(picker, 'change');
  await fire(upload, 'submit');
  assert.equal(h.calls.filter(call => call.method === 'POST').length, 1);
  finish({ id: 'created' }); await pending;
  assert.match(root.textContent, /Импорт создан, но список или записи не загрузились/);
  assert.equal(button(root, 'Загрузить JSON').disabled, true);
  detailFails = false;
  await fire(button(root, 'Обновить импорт'), 'click');
  assert.equal(h.calls.filter(call => call.method === 'POST').length, 1);
  picker.files = [file('second.json', '[]')]; await fire(picker, 'change');
  assert.equal(button(root, 'Загрузить JSON').disabled, false);
});

test('detail pagination and 409 require explicit GET; no duplicate POST or unrelated editor refresh', async () => {
  let fresh = false, attempts = 0, imported = 0;
  const h = await harness((method, url) => {
    if (method === 'POST') { attempts++; throw Object.assign(new Error('stale'), { status: 409 }); }
    if (url.startsWith('/api/testing/imports?')) return [{ id: 'batch', filename: 'batch.json', status: 'pending', total: 51, pending: 51 }];
    const offset = Number(new URL(url, 'https://test.invalid').searchParams.get('offset'));
    if (fresh) imported++;
    const items = Array.from({ length: offset ? 1 : 50 }, (_, n) => ({ id: `row-${offset + n}`, ordinal: offset + n + 1,
      status: 'pending', expected_token: 'v1', validation_errors: [], payload: { question: `Вопрос ${offset + n + 1}`, type: 'single', options: [] } }));
    return { filename: 'batch.json', status: 'pending', total: 51, pending: 51, items };
  });
  const root = await h.testing.renderQuestionImports();
  await fire(button(root, 'Открыть импорт'), 'click');
  assert.match(root.textContent, /Записи: 1–50 из 51/);
  await fire(button(root, 'Следующие записи'), 'click');
  assert.match(root.textContent, /Записи: 51–51 из 51.*Вопрос 51/s);
  assert.equal(button(root, 'Следующие записи').disabled, true);
  const approve = button(root, 'Одобрить запись');
  await fire(approve, 'click');
  assert.equal(attempts, 1);
  assert.equal(approve.disabled, true);
  await fire(approve, 'click'); assert.equal(attempts, 1);
  fresh = true;
  await fire(button(root, 'Обновить импорт'), 'click');
  assert.equal(imported, 1);
  assert.equal(attempts, 1);
});

test('import actions do not remount a dirty test builder in the KK tab', async () => {
  const h = await harness((method, url, body) => {
    if (url === '/api/testing/tests' || url.includes('/questions?')) return [];
    if (url.startsWith('/api/testing/imports?')) return [{ id: 'batch', filename: 'batch.json', status: 'validated', total: 1, pending: 1, invalid: 0, approved: 0, rejected: 0 }];
    if (method === 'GET' && url.startsWith('/api/testing/imports/')) return { filename: 'batch.json', status: 'validated', total: 1,
      pending: 1, invalid: 0, approved: 0, rejected: 0, items: [{ id: 'one', ordinal: 0, status: 'pending', expected_token: 'v1',
        validation_errors: [], payload: { external_id: 'q1', question: 'Вопрос', competency_code: 'K1',
          type: 'single', options: [{ id: 'a', text: 'Да', is_correct: true }, { id: 'b', text: 'Нет', is_correct: false }] } }] };
    if (method === 'POST' && url.endsWith('/review')) return { id: 'one', ordinal: 0, status: 'approved', expected_token: 'v2' };
    throw Error(`Unexpected ${method} ${url} ${JSON.stringify(body)}`);
  });
  const root = await h.testing.renderResponsibleTesting([{ competency_id: 'cid', name: 'Аналитика' }]);
  const builder = form(root, 'Создать тест');
  const title = all(builder, 'input')[0]; title.value = 'Несохранённый тест';
  await fire(button(root, 'Открыть импорт'), 'click');
  await fire(button(root, 'Одобрить запись'), 'click');
  assert.equal(form(root, 'Создать тест'), builder);
  assert.equal(title.value, 'Несохранённый тест');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('dirty import item requires inline discard before opening another import, refreshing or paging; decline makes no request', async () => {
  for (const route of ['Открыть импорт', 'Обновить импорт', 'Следующие записи']) {
    const h = await harness((method, url) => {
      if (url.startsWith('/api/testing/imports?')) return ['first', 'second'].map(id => ({ id, filename: `${id}.json`, status: 'validated', total: 51 }));
      const id = url.includes('/second?') ? 'second' : 'first';
      const offset = Number(new URL(url, 'https://test.invalid').searchParams.get('offset'));
      const items = Array.from({ length: offset ? 1 : 50 }, (_, index) => ({ id: `${id}-${index + offset}`, ordinal: index + offset + 1,
        status: 'pending', expected_token: 'v1', validation_errors: [], payload: { question: 'Исходный вопрос', type: 'single', options: [] } }));
      return { id, filename: `${id}.json`, status: 'validated', total: 51, items };
    });
    const root = await h.testing.renderQuestionImports();
    await fire(buttons(root, 'Открыть импорт')[0], 'click');
    const edit = form(root, 'Сохранить правки'), question = all(edit, 'textarea')[0];
    question.value = 'Не сохранено'; await fire(question, 'input');
    const target = route === 'Открыть импорт' ? buttons(root, route)[1] : button(root, route);
    const before = h.calls.length;
    await fire(target, 'click');
    assert.equal(h.calls.length, before, `${route}: request before confirmation`);
    assert.equal(form(root, 'Сохранить правки'), edit);
    assert.equal(question.value, 'Не сохранено');
    assert.match(root.textContent, /несохранённые правки/);
    await fire(button(root, 'Остаться в импорте'), 'click');
    assert.equal(h.calls.length, before, `${route}: request after decline`);
    assert.equal(form(root, 'Сохранить правки'), edit);
    await fire(target, 'click');
    await fire(button(root, 'Сбросить правки и продолжить'), 'click');
    assert.equal(h.calls.length, before + 1, `${route}: request after explicit discard`);
    assert.equal(button(root, 'Сбросить правки и продолжить'), undefined);
    if (route === 'Открыть импорт') assert.match(root.textContent, /second\.json.*Записи: 1–50/s);
    if (route === 'Следующие записи') assert.match(root.textContent, /Записи: 51–51 из 51/);
  }
});

test('successful item Save resets dirty and refresh needs no discard', async () => {
  const row = { id: 'one', ordinal: 1, status: 'pending', expected_token: 'v1', validation_errors: [],
    payload: { question: 'Исходный вопрос', type: 'single', options: [] } };
  const h = await harness((method, url, body) => {
    if (url.startsWith('/api/testing/imports?')) return [{ id: 'batch', filename: 'batch.json', status: 'validated', total: 1 }];
    if (method === 'PUT') { Object.assign(row, { edited_payload: body.edited_payload, expected_token: 'v2' }); return { ...row }; }
    return { filename: 'batch.json', status: 'validated', total: 1, items: [row] };
  });
  const root = await h.testing.renderQuestionImports();
  await fire(button(root, 'Открыть импорт'), 'click');
  const edit = form(root, 'Сохранить правки');
  const question = all(edit, 'textarea')[0]; question.value = 'Сохранённый вопрос'; await fire(question, 'input');
  await fire(edit, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'PUT').length, 1);
  const before = h.calls.length;
  await fire(button(root, 'Обновить импорт'), 'click');
  assert.equal(h.calls.length, before + 1);
  assert.equal(button(root, 'Сбросить правки и продолжить'), undefined);
  assert.match(root.textContent, /Сохранённый вопрос/);
});

test('saving one row does not discard a different dirty row', async () => {
  const items = [1, 2].map(n => ({ id: `row-${n}`, ordinal: n, status: 'pending', expected_token: 'v1', validation_errors: [],
    payload: { question: `Вопрос ${n}`, type: 'single', options: [] } }));
  const h = await harness((method, url, body) => {
    if (url.startsWith('/api/testing/imports?')) return [{ id: 'batch', filename: 'batch.json', total: 2 }];
    if (method === 'PUT') {
      const row = items.find(item => url.includes(item.id));
      Object.assign(row, { edited_payload: body.edited_payload, expected_token: 'v2' });
      return { ...row };
    }
    return { filename: 'batch.json', total: 2, items };
  });
  const root = await h.testing.renderQuestionImports();
  await fire(button(root, 'Открыть импорт'), 'click');
  const [first, second] = all(root, 'form').filter(n => button(n, 'Сохранить правки'));
  const other = all(second, 'textarea')[0]; other.value = 'Правки второй записи'; await fire(other, 'input');
  const changed = all(first, 'textarea')[0]; changed.value = 'Правки первой записи'; await fire(changed, 'input');
  await fire(first, 'submit');
  const before = h.calls.length;
  await fire(button(root, 'Обновить импорт'), 'click');
  assert.equal(h.calls.length, before);
  assert.equal(other.value, 'Правки второй записи');
  assert.equal(all(root, 'form').filter(n => button(n, 'Сохранить правки'))[1], second);
});

test('article picker searches protected pages with paging, displays title and space rather than IDs', async () => {
  const h = await harness((method, url) => {
    if (url.startsWith('/api/testing/imports/source-pages?')) {
      const query = new URL(url, 'https://local.invalid').searchParams;
      return { total: 21, items: [{ id: 'private-page-id', title: query.get('offset') === '0' ? 'Первая статья' : 'Другая статья',
        space_name: 'Наше пространство', current_revision_id: 'revision-1' }] };
    }
    if (url.startsWith('/api/testing/imports?')) return [];
    throw new Error(`Unexpected ${method} ${url}`);
  });
  const root = await h.testing.renderQuestionImports();
  assert.match(root.textContent, /Первая статья · Наше пространство/);
  assert.doesNotMatch(root.textContent, /private-page-id|revision-1/);
  const search = all(root, 'input').find(n => n.placeholder === 'Название статьи');
  search.value = ' обучение & тест '; await fire(search, 'input');
  await fire(button(root, 'Найти статьи'), 'click');
  await fire(button(root, 'Следующие статьи'), 'click');
  const queries = h.calls.filter(c => c.url.includes('/source-pages?')).map(c => new URL(c.url, 'https://local.invalid').searchParams);
  assert.deepEqual(queries.map(q => [q.get('q'), q.get('limit'), q.get('offset')]),
    [['', '20', '0'], ['обучение & тест', '20', '0'], ['обучение & тест', '20', '20']]);
  assert.match(root.textContent, /Другая статья · Наше пространство/);
  assert.ok(h.calls.every(c => c.url.startsWith('/api/testing/imports')));
});

test('manual response submits selected article revision and exact correct_answer without calling external services', async () => {
  const h = await harness((method, url) => {
    if (url.startsWith('/api/testing/imports/source-pages?')) return { items: [{ id: 'page-1', title: 'Правила',
      space_name: 'КК', current_revision_id: 'revision-42' }], total: 1 };
    if (url.startsWith('/api/testing/imports?')) return [];
    if (url === '/api/testing/imports/from-generated') return { id: 'generated-1' };
    if (url.startsWith('/api/testing/imports/generated-1?')) return { id: 'generated-1', filename: 'Ответ',
      payload: { kind: 'generated_response_v1', source: { page_id: 'page-1', revision_id: 'revision-42' } },
      total: 1, invalid: 1, items: [{ id: 'item-1', ordinal: 1, status: 'invalid',
        expected_token: 'v1', validation_errors: ['Нужна компетенция'], payload: { question: 'Вопрос?',
          options: ['Да', 'Нет'], correct_answer: 0 } }] };
    throw new Error(`Unexpected ${method} ${url}`);
  });
  const root = await h.testing.renderQuestionImports();
  assert.equal(button(root, 'Сгенерировать').disabled, true);
  assert.match(root.textContent, /Генерация через LLM пока не подключена: выберите статью и вставьте JSON-ответ вручную/);
  await fire(button(root, 'Правила · КК'), 'click');
  const generated = form(root, 'Импортировать JSON-ответ');
  const numbers = all(generated, 'input').filter(n => n.attrs.type === 'number');
  assert.deepEqual(numbers.map(n => n.value), ['5', '4']);
  numbers[0].value = '2'; numbers[1].value = '3';
  const [topics, response] = all(generated, 'textarea');
  topics.value = '  сервис  \nтестирование\n';
  response.value = '{"questions":[{"question":"Вопрос?","options":["Да","Нет"],"correct_answer":0}]}';
  await fire(generated, 'submit');
  const post = h.calls.filter(c => c.method === 'POST');
  assert.equal(post.length, 1);
  assert.equal(post[0].url, '/api/testing/imports/from-generated');
  assert.equal(JSON.stringify(post[0].body), JSON.stringify({ source_page_id: 'page-1', source_revision_id: 'revision-42',
    settings: { questions_count: 2, answers_count: 3, topics: ['сервис', 'тестирование'] },
    response: { questions: [{ question: 'Вопрос?', options: ['Да', 'Нет'], correct_answer: 0 }] } }));
  assert.match(root.textContent, /В ответе 1 вопросов, запрошено 2/);
  assert.match(root.textContent, /Записи: 1–1 из 1/);
  assert.match(root.textContent, /Источник выбран вручную, связь с LLM ещё не подтверждена/);
  assert.ok(h.calls.every(c => c.url.startsWith('/api/testing/imports')));
});

test('malformed and oversized responses and invalid settings never POST; optional response file works', async () => {
  const h = await harness((method, url) => url.includes('/source-pages?') ? { items: [{ id: 'p', title: 'Статья', space_name: 'Отдел', current_revision_id: 'r' }], total: 1 } :
    url === '/api/testing/imports/from-generated' ? { id: 'created' } :
      url.startsWith('/api/testing/imports?') ? [] : { filename: 'Ответ', total: 0, items: [] });
  const root = await h.testing.renderQuestionImports();
  const generated = form(root, 'Импортировать JSON-ответ');
  const text = all(generated, 'textarea')[1], fileInput = all(generated, 'input').find(n => n.attrs.type === 'file');
  text.value = '{'; await fire(generated, 'submit');
  assert.match(root.textContent, /Выберите статью/);
  await fire(button(root, 'Статья · Отдел'), 'click');
  await fire(generated, 'submit');
  assert.match(root.textContent, /JSON-ответ некорректен/);
  text.value = JSON.stringify({ questions: [] }) + ' '.repeat(524288);
  await fire(generated, 'submit');
  assert.match(root.textContent, /512 КиБ/);
  text.value = '{"questions":[]}';
  const count = all(generated, 'input').find(n => n.attrs.type === 'number');
  count.value = '31'; await fire(generated, 'submit');
  assert.match(root.textContent, /Проверьте параметры/);
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  count.value = '5'; fileInput.files = [file('reply.json', '{"questions":[]}')];
  await fire(generated, 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('generated import cannot discard dirty reviewer rows; KK builder remains mounted', async () => {
  const h = await harness((method, url) => {
    if (url === '/api/testing/tests' || url.startsWith('/api/testing/questions?')) return [];
    if (url.includes('/source-pages?')) return { items: [{ id: 'p', title: 'Статья', space_name: 'КК', current_revision_id: 'r' }], total: 1 };
    if (url.startsWith('/api/testing/imports?')) return [{ id: 'old', filename: 'old.json', total: 1 }];
    if (url === '/api/testing/imports/from-generated') return { id: 'new' };
    if (url.startsWith('/api/testing/imports/')) return { filename: 'old.json', total: 1, items: [{ id: 'row', status: 'pending',
      expected_token: 'v1', payload: { question: 'Старый', options: [] }, validation_errors: [] }] };
    throw new Error(`Unexpected ${method} ${url}`);
  });
  const root = await h.testing.renderResponsibleTesting([{ competency_id: 'c', code: 'K1', name: 'Компетенция' }]);
  const builder = form(root, 'Создать тест'), title = all(builder, 'input')[0]; title.value = 'Мой черновик';
  await fire(button(root, 'Открыть импорт'), 'click');
  const edit = form(root, 'Сохранить правки'), question = all(edit, 'textarea')[0];
  question.value = 'Несохранённая правка'; await fire(question, 'input');
  assert.equal(root.hasUnsavedChanges(), true);
  assert.ok(all(edit, 'option').some(o => o.attrs.value === 'K1'));
  await fire(button(root, 'Статья · КК'), 'click');
  all(form(root, 'Импортировать JSON-ответ'), 'textarea')[1].value = '{"questions":[]}';
  await fire(form(root, 'Импортировать JSON-ответ'), 'submit');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
  assert.equal(question.value, 'Несохранённая правка');
  await fire(button(root, 'Сбросить правки и продолжить'), 'click');
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
  assert.equal(form(root, 'Создать тест'), builder);
  assert.equal(title.value, 'Мой черновик');
});

test('generated source must be explicitly confirmed before approval, including after reload with pinned revision', async () => {
  const item = { id: 'row', status: 'pending', ordinal: 1, expected_token: 'v1', validation_errors: [],
    payload: { question: 'Raw response' }, edited_payload: { question: 'Сверенный вопрос', competency_code: 'K1', type: 'single',
      options: [{ id: '1', text: 'Верно', is_correct: true }, { id: '2', text: 'Неверно', is_correct: false }],
      source_page_id: 'page-1', source_revision_id: 'revision-1' } };
  const respond = (method, url, body) => {
    if (url.startsWith('/api/testing/imports/source-pages?')) return { items: [], total: 0 };
    if (url.startsWith('/api/testing/imports?')) return [{ id: 'generated-import', filename: 'generated-response.json', total: 1 }];
    if (method === 'GET' && url.startsWith('/api/testing/imports/generated-import?')) return {
      filename: 'generated-response.json', status: 'validated', total: 1,
      payload: { kind: 'generated_response_v1', source: { page_id: 'page-1', revision_id: 'revision-1', provenance: 'selection_only' } },
      items: [item] };
    if (method === 'POST' && url.endsWith('/review')) {
      assert.equal(body.source_confirmed, true);
      item.status = 'approved';
      return { ...item };
    }
    throw new Error(`Unexpected ${method} ${url}`);
  };
  // Fresh render simulates reopening an existing import with no in-memory generated IDs.
  for (let open = 0; open < 2; open++) {
    item.status = 'pending';
    const h = await harness(respond);
    const root = await h.testing.renderQuestionImports();
    await fire(button(root, 'Открыть импорт'), 'click');
    assert.match(root.textContent, /Источник выбран вручную, связь с LLM ещё не подтверждена/);
    const edit = form(root, 'Сохранить правки');
    const approve = button(edit, 'Одобрить запись');
    const confirmed = all(edit, 'input').filter(n => n.attrs.type === 'checkbox').at(-1);
    assert.match(edit.textContent, /лично сверил\(а\) вопрос с выбранной статьёй/);
    assert.equal(confirmed.checked, false);
    assert.equal(approve.disabled, false);
    await fire(approve, 'click');
    assert.equal(h.calls.filter(c => c.method === 'POST').length, 0);
    assert.match(edit.textContent, /подтвердите источник вручную/);
    confirmed.checked = true; await fire(confirmed, 'change');
    assert.equal(approve.disabled, false, 'checking confirmation must not make question edits dirty');
    await fire(approve, 'click');
    const review = h.calls.filter(c => c.method === 'POST');
    assert.equal(review.length, 1);
    assert.equal(JSON.stringify(review[0].body), JSON.stringify({ expected_token: 'v1', decision: 'approve', source_confirmed: true }));
    assert.equal(review[0].url, '/api/testing/imports/generated-import/items/row/review');
  }
});

test('ordinary JSON file imports keep old source confirmation rules; pinned manual source does not auto-confirm', async () => {
  const item = { id: 'manual-row', status: 'pending', expected_token: 'v1', validation_errors: [],
    payload: { question: 'Вопрос', competency_code: 'K1', type: 'single',
      options: [{ id: 'a', text: 'Да', is_correct: true }, { id: 'b', text: 'Нет', is_correct: false }],
      source_page_id: 'manual-page', source_revision_id: 'manual-revision' } };
  const h = await harness((method, url, body) => {
    if (url.startsWith('/api/testing/imports/source-pages?')) return { items: [], total: 0 };
    if (url.startsWith('/api/testing/imports?')) return [{ id: 'manual-import', filename: 'manual.json', total: 1 }];
    if (method === 'GET' && url.startsWith('/api/testing/imports/manual-import?')) return {
      filename: 'manual.json', status: 'validated', total: 1, payload: { questions: [item.payload] }, items: [item] };
    if (method === 'POST' && url.endsWith('/review')) { item.status = 'approved'; return { ...item }; }
    throw new Error(`Unexpected ${method} ${url} ${JSON.stringify(body)}`);
  });
  const root = await h.testing.renderQuestionImports();
  await fire(button(root, 'Открыть импорт'), 'click');
  const edit = form(root, 'Сохранить правки');
  assert.match(edit.textContent, /Подтверждаю источник без указанной версии/);
  assert.doesNotMatch(edit.textContent, /лично сверил/);
  await fire(button(edit, 'Одобрить запись'), 'click');
  const review = h.calls.find(c => c.method === 'POST');
  assert.equal(JSON.stringify(review.body), JSON.stringify({ expected_token: 'v1', decision: 'approve' }));
  assert.doesNotMatch(root.textContent, /Источник выбран вручную, связь с LLM ещё не подтверждена/);
});
