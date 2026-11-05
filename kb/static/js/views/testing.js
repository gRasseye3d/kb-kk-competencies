import { api, el, navigate, state } from '../app.js';

// Shared module components. Identifiers stay in values, requests and routes only.
export const empty = text => el('p', { class: 'empty-state' }, text);
export const field = (title, input) => el('label', { class: 'field' }, title, input);
export const card = (title, ...children) => el('section', { class: 'cm-card' }, el('h3', {}, title), ...children);
export const input = (maxLength = 200) => el('input', { required: '', maxlength: maxLength });
export function select(items, placeholder = 'Выберите…') {
  return el('select', { required: '' }, el('option', { value: '' }, placeholder),
    ...items.map(item => el('option', { value: item.id }, item.name || 'Без названия')));
}
const statuses = { draft: 'Черновик', returned: 'Возвращена', pending_approval: 'На утверждении',
  approved: 'Утверждено', archived: 'Архив', pending: 'На проверке', rejected: 'Отклонён',
  published: 'Опубликован', assigned: 'Назначен', in_progress: 'В процессе', completed: 'Завершён', graded: 'Оценён' };
export const badge = status => el('span', { class: `status-badge status-${status}` }, statuses[status] || 'Статус неизвестен');
export const dateLabel = value => value && !Number.isNaN(new Date(value).getTime())
  ? new Date(value).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : 'Не указан';
export function errorText(error) {
  if (error.code === 'assessment_unavailable') {
    const competencies = error.details?.competencies || [];
    return 'Начать тест пока нельзя. ' + competencies.map(c => {
      const reason = c.reason === 'reserved_active'
        ? 'есть другая незавершённая попытка. Завершите её перед повторным стартом.'
        : `период ожидания 30 дней${c.available_at ? '. Повторная оценка доступна с ' + dateLabel(c.available_at) : ''}.`;
      return `${c.competency_name || 'Компетенция'} — ${reason}`;
    }).join(' ');
  }
  const messages = {
    'Two approved questions per competency required': 'Для каждой компетенции нужны минимум два одобренных вопроса.',
    'No competencies currently available': 'Компетенции пока недоступны: действует период ожидания или есть незавершённая попытка.',
    'Deadline passed': 'Срок выполнения теста истёк.',
    'Deadline must be in the future': 'Укажите срок в будущем.',
    'Department required': 'Сначала необходимо назначить подразделение.',
    'Not department responsible': 'Вы не назначены ответственным за это подразделение.',
    'Not department manager': 'Вы не назначены руководителем этого подразделения.',
    'Role required': 'Недостаточно прав для этого действия.',
    'Empty profile': 'Добавьте хотя бы одну компетенцию в карту.'
  };
  return messages[error.message] || (error.message || 'Не удалось выполнить запрос.').replace(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, 'объект');
}
export async function request(method, url, body) {
  const result = await api(method, url, body);
  // app.api intentionally returns a value for wiki 404s; module operations must fail.
  if (result?.status === 404 && result.error) throw new Error('Данные не найдены. Обновите страницу.');
  return result;
}
export function notice(text = '', error = false) {
  return el('p', { class: `cm-notice${error ? ' is-error' : ''}`, role: 'status', 'aria-live': 'polite' }, text);
}
export function actionForm(label, children, action, refresh) {
  const status = notice();
  const submit = el('button', { class: 'btn', type: 'submit' }, label);
  const form = el('form', { class: 'cm-form' }, ...children, el('div', { class: 'cm-actions' }, submit), status);
  let busy = false;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || submit.disabled || !form.reportValidity()) return;
    busy = true;
    submit.disabled = true;
    status.className = 'cm-notice';
    status.replaceChildren('Сохранение…');
    let conflict = false;
    try {
      const result = await action();
      status.replaceChildren('Готово.');
      if (refresh && result !== false) await refresh('Изменения сохранены.');
    } catch (error) {
      conflict = error.status === 409 && error.code !== 'assessment_unavailable';
      status.className = 'cm-notice is-error';
      status.replaceChildren(conflict ? 'Данные изменились. Обновите их и проверьте перед повторным действием.' : errorText(error));
      if (conflict && refresh) status.append(el('button', { type: 'button', class: 'btn secondary',
        onclick: () => refresh('Данные обновлены. Проверьте актуальное состояние.') }, 'Обновить данные'));
    } finally {
      busy = false;
      submit.disabled = conflict;
    }
  });
  return form;
}

export function resultCards(rows, own = false) {
  const grid = el('div', { class: 'evaluation-grid' });
  const groups = new Map();
  for (const row of rows) {
    if (!groups.has(row.assignment_id)) groups.set(row.assignment_id, []);
    groups.get(row.assignment_id).push(row);
  }
  for (const results of groups.values()) {
    const row = results[0];
    const box = card(row.test_title || 'Назначенный тест', badge(row.status),
      el('p', { class: 'cm-muted' }, `Назначен: ${dateLabel(row.assigned_at)} · Срок: ${dateLabel(row.due_at)}`));
    for (const result of results.filter(r => r.competency_id)) box.append(el('div', { class: 'cm-result' },
      el('strong', {}, result.competency_name || 'Компетенция'),
      el('span', {}, `Уровень ${result.level} · верно ${result.correct_count} из ${result.total_count}`)));
    if (own && state.user.roles.includes('employee')) {
      if (row.status === 'assigned') box.append(actionForm('Начать тест', [], async () => {
        const attempt = await request('POST', `/api/testing/assignments/${row.assignment_id}/start`, {});
        navigate(`#/testing?attempt=${encodeURIComponent(attempt.id)}`);
        return false;
      }, () => navigate('#/testing')));
      if (row.status === 'in_progress' && row.attempt_id) box.append(el('a', { class: 'btn',
        href: `#/testing?attempt=${encodeURIComponent(row.attempt_id)}` }, 'Продолжить тест'));
    }
    grid.append(box);
  }
  if (!rows.length) grid.append(empty('Назначенных тестов пока нет.'));
  return grid;
}

async function attemptCard(attempt) {
  const view = await request('GET', `/api/testing/attempts/${encodeURIComponent(attempt)}`);
  const box = card(view.test_title || 'Прохождение теста', badge(view.status));
  box.append(el('ul', { class: 'cm-list' }, ...view.competencies.map(c => el('li', {},
    el('strong', {}, c.competency_name || 'Компетенция'), ' — ', c.state === 'included' ? 'Включена в тест' :
      c.exclusion_reason === 'reserved_active' ? 'Исключена: другая незавершённая попытка' :
        `Исключена: период ожидания${c.available_at ? ' до ' + dateLabel(c.available_at) : ''}`))));
  if (view.status !== 'in_progress') {
    box.append(el('a', { href: '#/testing', class: 'btn secondary' }, 'К результатам'));
    return box;
  }
  const answers = {};
  const questions = view.questions.map((q, index) => {
    const controls = q.options.map(o => {
      const control = el('input', { type: q.type === 'single' ? 'radio' : 'checkbox', name: q.id, value: o.id });
      return { id: o.id, control, label: el('label', { class: 'cm-choice' }, control, o.body) };
    });
    answers[q.id] = controls;
    return el('fieldset', {}, el('legend', {}, `${index + 1}. ${q.body}`),
      el('p', { class: 'cm-muted' }, q.type === 'single' ? 'Выберите один ответ' : 'Выберите все верные ответы'),
      ...controls.map(c => c.label));
  });
  box.append(el('p', { class: 'cm-muted' }, 'Ответы сохраняются при отправке. До отправки не закрывайте страницу.'),
    actionForm('Отправить ответы', questions, async () => {
      const payload = Object.fromEntries(Object.entries(answers).map(([qid, controls]) =>
        [qid, controls.filter(c => c.control.checked).map(c => c.id)]));
      if (Object.values(payload).some(options => !options.length)) throw new Error('Ответьте на каждый вопрос.');
      await request('POST', `/api/testing/attempts/${encodeURIComponent(attempt)}/submit`, { answers: payload });
      navigate('#/testing');
      return false;
    }, () => navigate('#/testing')));
  return box;
}

// Default route and employee tab share the same cards; authoring lives in its role tab.
export async function renderTesting({ embedded = false } = {}) {
  const box = el('section', { class: embedded ? 'cm-testing' : 'competency-module' });
  if (!embedded) box.append(el('a', { href: '#/competencies' }, '← Карта компетенций'));
  box.append(el('h2', {}, 'Мои тесты и результаты'));
  try {
    const attempt = new URLSearchParams(location.hash.split('?')[1] || '').get('attempt');
    if (attempt) box.append(await attemptCard(attempt));
    else box.append(resultCards(await request('GET', '/api/testing/results'), true));
  } catch (error) { box.append(notice(errorText(error), true)); }
  return box;
}

export async function renderManagerTesting(department, refresh) {
  const [members, tests] = await Promise.all([
    request('GET', `/api/competencies/departments/${department.id}/members`),
    request('GET', '/api/testing/tests')
  ]);
  const people = members.map(m => ({ id: m.id, name: `${m.display_name || m.login || 'Сотрудник'}${m.login && m.display_name ? ' · ' + m.login : ''}` }));
  const employee = select(people, 'Выберите сотрудника');
  const test = select(tests.filter(t => t.status === 'published').map(t => ({ id: t.id, name: `${t.title || 'Тест'} · версия ${t.version || 1}` })), 'Выберите опубликованный тест');
  const deadline = el('input', { type: 'datetime-local' });
  const assignment = card('Назначить тест', actionForm('Назначить', [field('Сотрудник', employee), field('Тест', test),
    field('Срок выполнения (необязательно)', deadline)], async () => {
    const due = deadline.value ? new Date(deadline.value) : null;
    if (due && (Number.isNaN(due.getTime()) || due <= new Date())) throw new Error('Укажите срок в будущем.');
    await request('POST', '/api/testing/assignments', { user_id: employee.value, test_id: test.value, due_at: due ? due.toISOString() : null });
  }, refresh));
  if (!members.length || !tests.some(t => t.status === 'published')) assignment.append(empty('Для назначения нужны сотрудники отдела и опубликованный тест.'));
  const target = select(people, 'Выберите сотрудника');
  const results = el('div');
  const history = card('Результаты сотрудника', actionForm('Показать результаты', [field('Сотрудник', target)], async () => {
    const rows = await request('GET', `/api/testing/results?user_id=${encodeURIComponent(target.value)}`);
    results.replaceChildren(resultCards(rows));
  }), results);
  return el('div', { class: 'cm-stack' }, assignment, history);
}

export async function renderResponsibleTesting(competencies, refresh) {
  const [bank, tests] = await Promise.all([request('GET', '/api/testing/questions'), request('GET', '/api/testing/tests')]);
  const root = el('div', { class: 'cm-stack' });
  if (!competencies.length) root.append(empty('Для создания вопросов и тестов нужна утверждённая активная карта отдела.'));
  const available = competencies.map(c => ({ id: c.competency_id, name: c.name }));
  const competency = select(available, 'Выберите компетенцию');
  const body = el('textarea', { required: '', maxlength: 5000, rows: 3 });
  const type = select([{ id: 'single', name: 'Один верный ответ' }, { id: 'multiple', name: 'Несколько верных ответов' }]);
  type.value = 'single';
  const options = [];
  const optionsBox = el('div', { class: 'cm-stack' });
  const addOption = () => {
    if (options.length >= 20) return;
    const text = input(1000);
    const correct = el('input', { type: 'checkbox' });
    const option = { text, correct };
    const row = el('div', { class: 'cm-option-row' }, field('Вариант ответа', text),
      el('label', { class: 'cm-choice' }, correct, 'Верный'), el('button', { class: 'btn secondary', type: 'button', onclick: () => {
        if (options.length <= 2) return;
        options.splice(options.indexOf(option), 1);
        row.remove();
      } }, 'Удалить'));
    options.push(option);
    optionsBox.append(row);
  };
  addOption(); addOption();
  const questionForm = actionForm('Сохранить вопрос', [field('Компетенция активной карты', competency), field('Текст вопроса', body),
    field('Тип вопроса', type), optionsBox, el('button', { type: 'button', class: 'btn secondary', onclick: addOption }, 'Добавить вариант')], async () => {
    const correctCount = options.filter(o => o.correct.checked).length;
    if (!correctCount || (type.value === 'single' && correctCount !== 1)) throw new Error('Отметьте верные ответы: для одиночного выбора — ровно один.');
    await request('POST', '/api/testing/questions', { competency_id: competency.value, body: body.value.trim(), type: type.value,
      options: options.map(o => ({ body: o.text.value.trim(), correct: o.correct.checked })) });
  }, refresh);
  const title = input(250);
  const choices = available.map(c => ({ id: c.id, name: c.name, control: el('input', { type: 'checkbox', value: c.id }) }));
  const testForm = actionForm('Создать тест', [field('Название теста', title),
    el('fieldset', {}, el('legend', {}, 'От 2 до 20 компетенций активной карты'),
      ...choices.map(c => el('label', { class: 'cm-choice' }, c.control, c.name)))], async () => {
    const ids = choices.filter(c => c.control.checked).map(c => c.id);
    if (ids.length < 2 || ids.length > 20) throw new Error('Выберите от 2 до 20 компетенций.');
    await request('POST', '/api/testing/tests', { title: title.value.trim(), competency_ids: ids });
  }, refresh);
  root.append(el('div', { class: 'manager-grid' }, card('Новый вопрос', questionForm), card('Новый тест', testForm,
    el('p', { class: 'cm-muted' }, 'Для публикации нужны минимум два одобренных вопроса на каждую компетенцию. Опубликованная версия неизменяема.'))));
  const testList = el('div', { class: 'evaluation-grid' });
  for (const t of tests) {
    const box = card(t.title || 'Тест', badge(t.status), el('p', { class: 'cm-muted' }, t.version ? `Версия ${t.version}` : 'Не опубликован'),
      el('ul', { class: 'cm-list' }, ...(t.competencies || []).map(c => el('li', {}, c.name || 'Компетенция'))));
    if (t.status === 'draft') box.append(actionForm('Опубликовать', [], () => request('POST', `/api/testing/tests/${t.id}/publish`, {}), refresh));
    testList.append(box);
  }
  root.append(el('h2', {}, 'Тесты подразделения'), tests.length ? testList : empty('Тесты ещё не созданы.'), el('h2', {}, 'Банк вопросов'));
  const bankList = el('div', { class: 'evaluation-grid' });
  for (const q of bank) {
    const name = q.competency_name || available.find(c => c.id === q.competency_id)?.name || 'Компетенция';
    const detail = el('div');
    const box = card(q.body, el('p', { class: 'cm-muted' }, name), badge(q.moderation_status));
    box.append(actionForm('Варианты и ключ', [], async () => {
      const data = await request('GET', `/api/testing/questions/${q.id}`);
      detail.replaceChildren(el('ul', { class: 'cm-list' }, ...data.options.map(o => el('li', {},
        o.body, el('span', { class: `status-badge${o.is_correct ? ' status-approved' : ''}` }, o.is_correct ? 'Верный' : 'Неверный')))));
    }), detail);
    if (q.moderation_status === 'pending') box.append(el('div', { class: 'cm-actions' },
      actionForm('Одобрить', [], () => request('POST', `/api/testing/questions/${q.id}/review`, { decision: 'approve' }), refresh),
      actionForm('Отклонить', [], () => request('POST', `/api/testing/questions/${q.id}/review`, { decision: 'reject' }), refresh)));
    bankList.append(box);
  }
  root.append(bank.length ? bankList : empty('Вопросов в банке пока нет.'));
  return root;
}
