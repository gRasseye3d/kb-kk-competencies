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
  invalid: 'Ошибки проверки', validated: 'Ожидает проверки', processed: 'Проверен',
  published: 'Опубликован', assigned: 'Назначен', in_progress: 'В процессе', completed: 'Завершён', graded: 'Оценён' };
export const badge = status => el('span', { class: `status-badge status-${status}` }, statuses[status] || 'Статус неизвестен');
export const dateLabel = value => value && !Number.isNaN(new Date(value).getTime())
  ? new Date(value).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : 'Не указан';
export function errorText(error) {
  if (error.status === 428) return 'Не хватает данных для проверки актуальности. Обновите данные перед сохранением.';
  if (error.code === 'due_missing') return 'Срок назначения не указан. Начать тест нельзя. Обратитесь к руководителю.';
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
    'Deadline required': 'Укажите срок выполнения теста.',
    'Deadline must be future ISO 8601 with timezone': 'Укажите корректный срок выполнения с часовым поясом.',
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
      conflict = (error.status === 409 && !['assessment_unavailable', 'due_missing'].includes(error.code)) || error.status === 428;
      status.className = 'cm-notice is-error';
      status.replaceChildren(error.status === 428 ? errorText(error) : conflict ? 'Данные изменились. Обновите их и проверьте перед повторным действием.' : errorText(error));
      if (conflict && refresh) {
        const reload = el('button', { type: 'button', class: 'btn secondary', onclick: async () => {
          if (busy) return;
          busy = true;
          try {
            if (await refresh('Данные обновлены. Проверьте актуальное состояние.') !== false) {
              submit.disabled = false;
              status.className = 'cm-notice';
              status.replaceChildren('Данные обновлены. Проверьте актуальное состояние.');
            }
          } catch (loadError) { status.replaceChildren(errorText(loadError), reload); }
          finally { busy = false; }
        } }, 'Обновить данные');
        status.append(reload);
      }
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
      ...(row.test_version ? [el('p', { class: 'cm-muted' }, `Версия ${row.test_version}`)] : []),
      el('p', { class: 'cm-muted' }, `Назначен: ${dateLabel(row.assigned_at)} · Срок: ${dateLabel(row.due_at)}`));
    for (const result of results.filter(r => r.competency_id)) box.append(el('div', { class: 'cm-result' },
      el('strong', {}, result.competency_name || 'Компетенция'),
      el('span', {}, `Уровень ${result.level} · верно ${result.correct_count} из ${result.total_count}`)));
    if (own && state.user.roles.includes('employee')) {
      const dueMissing = row.assignment_type === 'manager_assigned' && !row.due_at;
      if (row.status === 'assigned' && dueMissing) box.append(notice(errorText({ code: 'due_missing' }), true));
      if (row.status === 'assigned' && !dueMissing) box.append(actionForm('Начать тест', [], async () => {
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
  if (view.test_version) box.append(el('p', { class: 'cm-muted' }, `Версия ${view.test_version}`));
  box.append(el('ul', { class: 'cm-list' }, ...(view.competencies || []).map(c => el('li', {},
    el('strong', {}, c.competency_name || 'Компетенция'), ' — ', c.state === 'included' ? 'Включена в тест' :
      c.exclusion_reason === 'reserved_active' ? 'Исключена: другая незавершённая попытка' :
        `Исключена: период ожидания${c.available_at ? ' до ' + dateLabel(c.available_at) : ''}`))));
  if (view.status !== 'in_progress') {
    box.append(el('a', { href: '#/testing', class: 'btn secondary' }, 'К результатам'));
    return box;
  }
  const answers = {};
  let index = 0;
  const questionView = q => {
    const controls = (q.options || []).map(o => {
      const control = el('input', { type: q.type === 'single' ? 'radio' : 'checkbox', name: q.id, value: o.id });
      return { id: o.id, control, label: el('label', { class: 'cm-choice' }, control, o.body) };
    });
    answers[q.id] = controls;
    return el('fieldset', {}, el('legend', {}, `${++index}. ${q.body}`),
      el('p', { class: 'cm-muted' }, q.type === 'single' ? 'Выберите один ответ' : 'Выберите все верные ответы'),
      ...controls.map(c => c.label));
  };
  // Presence of blocks, including an empty array, is authoritative. Old attempts stay readable.
  const questions = Array.isArray(view.blocks) ? view.blocks.flatMap(block => {
    const included = (block.competencies || []).filter(c => (c.questions || []).length);
    return included.length ? [el('section', { class: 'cm-test-block' }, el('h3', {}, block.title || 'Блок'),
      ...included.map(c => el('section', {}, el('h4', {}, c.competency_name || 'Компетенция'),
        ...c.questions.map(questionView))))] : [];
  }) : (view.questions || []).map(questionView);
  box.append(el('p', { class: 'cm-muted' }, 'Ответы сохраняются при отправке. До отправки не закрывайте страницу. Вопросы без ответа считаются неверными. Повторная отправка невозможна.'),
    actionForm('Отправить ответы', questions, async () => {
      const payload = Object.fromEntries(Object.entries(answers).map(([qid, controls]) =>
        [qid, controls.filter(c => c.control.checked).map(c => c.id)]));
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
  if (!state.user.roles.includes('manager')) return notice('Недостаточно прав для назначения тестов.', true);
  const [members, tests] = await Promise.all([
    request('GET', `/api/competencies/departments/${department.id}/members`),
    request('GET', '/api/testing/tests')
  ]);
  const people = members.map(m => ({ id: m.id, name: `${m.display_name || m.login || 'Сотрудник'}${m.login && m.display_name ? ' · ' + m.login : ''}` }));
  const employee = select(people, 'Выберите сотрудника');
  const assignable = tests.filter(t => t.assignable);
  const test = select(assignable.map(t => ({ id: t.id, name: `${t.title || 'Тест'} · опубликована версия ${t.latest_published_version || t.version}` })), 'Выберите опубликованный тест');
  const version = el('select', {}, el('option', { value: '' }, 'Последняя опубликованная на момент назначения'));
  const preview = el('div');
  const versionStatus = notice();
  let versionLoading = false;
  let versionRequest = 0;
  test.addEventListener('change', async () => {
    const generation = ++versionRequest;
    version.value = '';
    version.replaceChildren(el('option', { value: '' }, 'Последняя опубликованная на момент назначения'));
    preview.replaceChildren();
    versionStatus.replaceChildren();
    version.disabled = versionLoading = Boolean(test.value);
    if (!test.value) return;
    try {
      const versions = await request('GET', `/api/testing/tests/${encodeURIComponent(test.value)}/versions`);
      if (generation !== versionRequest) return;
      version.append(...versions.map(v => el('option', { value: v.version }, `Версия ${v.version} · ${dateLabel(v.published_at)}`)));
    } catch (error) {
      if (generation === versionRequest) versionStatus.replaceChildren(errorText(error));
    } finally {
      if (generation === versionRequest) version.disabled = versionLoading = false;
    }
  });
  const deadline = el('input', { type: 'datetime-local', required: '' });
  const assignment = card('Назначить тест', actionForm('Назначить', [field('Сотрудник', employee), field('Тест', test),
    field('Опубликованная версия', version), versionStatus,
    field('Срок выполнения (обязательно, местное время)', deadline)], async () => {
    if (versionLoading) throw new Error('Дождитесь загрузки версий.');
    if (!employee.value || !assignable.some(t => t.id === test.value)) throw new Error('Выберите сотрудника и опубликованный тест.');
    if (!deadline.value) throw new Error('Укажите срок выполнения теста.');
    const due = new Date(deadline.value);
    if (Number.isNaN(due.getTime()) || due <= new Date()) throw new Error('Укажите срок в будущем.');
    await request('POST', '/api/testing/assignments', { user_id: employee.value, test_id: test.value,
      ...(version.value ? { test_version: Number(version.value) } : {}), due_at: due.toISOString() });
  }, refresh));
  assignment.append(actionForm('Просмотреть опубликованные версии', [], async () => {
    if (!test.value) throw new Error('Выберите тест.');
    preview.replaceChildren(await renderTestVersions(test.value));
  }), preview);
  if (!members.length || !assignable.length) assignment.append(empty('Для назначения нужны сотрудники отдела и опубликованный тест.'));
  const target = select(people, 'Выберите сотрудника');
  const results = el('div');
  const history = card('Результаты сотрудника', actionForm('Показать результаты', [field('Сотрудник', target)], async () => {
    const rows = await request('GET', `/api/testing/results?user_id=${encodeURIComponent(target.value)}`);
    results.replaceChildren(resultCards(rows));
  }), results);
  return el('div', { class: 'cm-stack' }, assignment, history);
}

export async function renderResponsibleTesting(competencies, refresh) {
  if (!state.user.roles.includes('kk_responsible')) return notice('Черновики доступны только ответственному за карту компетенций.', true);
  const tests = await request('GET', '/api/testing/tests');
  const root = el('div', { class: 'cm-stack' });
  const builders = new Set();
  let imports;
  root.hasUnsavedChanges = () => [...builders].some(builder => builder.hasUnsavedChanges()) || Boolean(imports?.hasUnsavedChanges());
  const refreshNotice = notice();
  const refreshTab = async (message, explicitlyDiscardedBuilder = null, discardAll = false) => {
    if (!discardAll && [...builders].some(builder => builder !== explicitlyDiscardedBuilder && builder.hasUnsavedChanges())) {
      refreshNotice.replaceChildren('Сначала сохраните изменения всех открытых тестов. Обновление вкладки не выполнено: несохранённые изменения остаются в редакторах.');
      return false;
    }
    refreshNotice.replaceChildren();
    if (refresh) await refresh(message, discardAll);
  };
  // Bank actions must not replace this tab: it can contain several unsaved editors.
  const refreshQuestions = async () => {
    await Promise.all([loadBank(), ...[...builders].map(builder => builder.refreshQuestionChoices())]);
  };
  root.append(refreshNotice);
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
  }, refreshQuestions);
  const newBuilder = await renderTestBuilder(competencies, null, refreshTab);
  builders.add(newBuilder);
  root.append(card('Новый вопрос', questionForm), card('Новый тест', newBuilder));
  const testList = el('div', { class: 'cm-stack' });
  for (const t of tests) {
    const metadata = el('p', { class: 'cm-muted' }, testMetadata(t));
    const editor = el('div');
    let openedBuilder = null;
    const preview = el('div');
    const box = card(t.title || 'Тест', badge(t.status), metadata,
      el('ul', { class: 'cm-list' }, ...(t.competencies || []).map(c => el('li', {}, c.name || 'Компетенция'))));
    if (t.status !== 'archived') box.append(actionForm(t.has_draft ? 'Редактировать черновик' : 'Создать новую версию', [], async () => {
      // Reopening the same card must not replace a dirty editor either.
      if (openedBuilder) return false;
      const draft = await request(t.has_draft ? 'GET' : 'POST', `/api/testing/tests/${encodeURIComponent(t.id)}/draft`,
        t.has_draft ? undefined : { expected_token: t.expected_token });
      Object.assign(t, draft, { has_draft: true });
      metadata.replaceChildren(testMetadata(t));
      openedBuilder = await renderTestBuilder(competencies, draft, refreshTab, saved => {
        Object.assign(t, saved, { has_draft: true });
        metadata.replaceChildren(testMetadata(t));
      });
      builders.add(openedBuilder);
      editor.replaceChildren(openedBuilder);
      return false;
    }, refreshTab));
    if (t.latest_published_version || t.version) box.append(actionForm('Просмотреть опубликованные версии', [], async () => {
      preview.replaceChildren(await renderTestVersions(t.id));
    }));
    if (t.status !== 'archived') {
      const warning = notice();
      const archive = () => request('POST', `/api/testing/tests/${encodeURIComponent(t.id)}/archive`,
        { expected_token: t.expected_token });
      box.append(actionForm('Архивировать тест', [], () => {
        if (root.hasUnsavedChanges()) {
          warning.className = 'cm-notice is-error';
          warning.replaceChildren('Есть несохранённые изменения тестов. Сначала сохраните их или явно подтвердите сброс перед архивацией.',
            actionForm('Сбросить правки и архивировать', [], archive, message => refreshTab(message, null, true)));
          throw new Error('Архивация отменена: есть несохранённые изменения.');
        }
        return archive();
      }, refreshTab), warning);
    }
    box.append(editor, preview);
    testList.append(box);
  }
  root.append(el('h2', {}, 'Тесты подразделения'), tests.length ? testList : empty('Тесты ещё не созданы.'),
    el('button', { type: 'button', class: 'btn secondary', onclick: async () => {
      try { await refreshTab('Список тестов обновлён.'); }
      catch (error) { refreshNotice.replaceChildren(errorText(error)); }
    } }, 'Обновить список тестов'),
    el('h2', {}, 'Банк вопросов'));
  const bankList = el('div', { class: 'evaluation-grid' });
  const bankCompetency = select(available, 'Все компетенции');
  bankCompetency.required = false;
  const bankStatus = select([{ id: 'approved', name: 'Утверждённые' }, { id: 'pending', name: 'На проверке' },
    { id: 'rejected', name: 'Отклонённые' }], 'Все статусы');
  bankStatus.required = false;
  let offset = 0;
  let bankRequest = 0;
  const pageStatus = notice();
  const previous = el('button', { type: 'button', class: 'btn secondary' }, 'Предыдущая страница вопросов');
  const next = el('button', { type: 'button', class: 'btn secondary' }, 'Следующая страница вопросов');
  const loadBank = async () => {
    const generation = ++bankRequest;
    previous.disabled = next.disabled = true;
    try {
      const query = new URLSearchParams({ limit: '100', offset: String(offset) });
      if (bankCompetency.value) query.set('competency_id', bankCompetency.value);
      if (bankStatus.value) query.set('status', bankStatus.value);
      const bank = await request('GET', `/api/testing/questions?${query}`);
      if (generation !== bankRequest) return;
      bankList.replaceChildren();
      pageStatus.replaceChildren(`Вопросы: ${offset + (bank.length ? 1 : 0)}–${offset + bank.length}`);
      for (const q of bank) {
        const name = q.competency_name || available.find(c => c.id === q.competency_id)?.name || 'Компетенция';
        const detail = el('div');
        const box = card(q.body, el('p', { class: 'cm-muted' }, name), badge(q.moderation_status));
        box.append(actionForm('Варианты и ключ', [], async () => {
          const data = await request('GET', `/api/testing/questions/${q.id}`);
          detail.replaceChildren(...(data.import_id ? [el('p', { class: 'cm-muted' }, 'Источник: ',
            el('a', { href: `#/competencies?tab=responsible&import=${encodeURIComponent(data.import_id)}` }, 'Запись импорта'),
            ` · ${data.import_item_id}`)] : []),
          ...(data.source_page_id ? [el('p', { class: 'cm-muted' }, 'Статья-источник: ',
            el('a', { href: `#/page/${encodeURIComponent(data.source_page_id)}` }, 'Открыть статью'))] : []),
          el('ul', { class: 'cm-list' }, ...data.options.map(o => el('li', {},
            o.body, el('span', { class: `status-badge${o.is_correct ? ' status-approved' : ''}` }, o.is_correct ? 'Верный' : 'Неверный')))));
        }), detail);
        if (q.moderation_status === 'pending') box.append(el('div', { class: 'cm-actions' },
          actionForm('Одобрить', [], () => request('POST', `/api/testing/questions/${q.id}/review`, { decision: 'approve' }), refreshQuestions),
          actionForm('Отклонить', [], () => request('POST', `/api/testing/questions/${q.id}/review`, { decision: 'reject' }), refreshQuestions)));
        bankList.append(box);
      }
      if (!bank.length) bankList.append(empty('По этим фильтрам вопросов нет.'));
      previous.disabled = offset === 0;
      next.disabled = bank.length < 100;
    } catch (error) { if (generation === bankRequest) pageStatus.replaceChildren(errorText(error)); }
  };
  previous.addEventListener('click', async () => { if (!previous.disabled) { offset = Math.max(0, offset - 100); await loadBank(); } });
  next.addEventListener('click', async () => { if (!next.disabled) { offset += 100; await loadBank(); } });
  root.append(actionForm('Найти вопросы', [field('Компетенция банка', bankCompetency), field('Статус вопроса', bankStatus)], async () => {
    offset = 0; await loadBank();
  }), bankList, el('div', { class: 'cm-actions' }, previous, next), pageStatus);
  await loadBank();
  imports = await renderQuestionImports(refreshQuestions, competencies);
  root.append(imports);
  return root;
}

// This controller owns its own DOM: refreshing an import must never replace test builders.
export async function renderQuestionImports(onApproved = () => {}, competencies = []) {
  if (!state.user.roles.includes('kk_responsible')) return notice('Импорт доступен только ответственному за карту компетенций.', true);
  const listSize = 20, itemSize = 50, maxBytes = 512 * 1024;
  const fileInput = el('input', { type: 'file', accept: '.json,application/json', 'aria-label': 'Файл JSON (UTF-8, до 512 КиБ)' });
  const uploadMessage = notice();
  const uploadButton = el('button', { type: 'submit', class: 'btn' }, 'Загрузить JSON');
  const upload = el('form', { class: 'cm-form cm-import-upload' }, field('Файл JSON (UTF-8, до 512 КиБ)', fileInput),
    el('div', { class: 'cm-actions' }, uploadButton), uploadMessage);
  const list = el('div', { class: 'cm-stack' }), listMessage = notice(), detail = el('div');
  const navigationNotice = el('div', { class: 'cm-notice', role: 'alert' });
  const visibleEdits = new Map();
  const confirmReplace = action => {
    if (![...visibleEdits.values()].some(editor => editor.hasUnsavedChanges())) {
      navigationNotice.replaceChildren();
      return action();
    }
    navigationNotice.replaceChildren('Есть несохранённые правки записей импорта. Сохраните их или явно подтвердите сброс.',
      el('button', { type: 'button', class: 'btn secondary', onclick: async () => {
        navigationNotice.replaceChildren();
        await action();
      } }, 'Сбросить правки и продолжить'),
      el('button', { type: 'button', class: 'btn secondary', onclick: () => navigationNotice.replaceChildren() }, 'Остаться в импорте'));
    return false;
  };
  const listPrevious = el('button', { type: 'button', class: 'btn secondary' }, 'Предыдущие импорты');
  const listNext = el('button', { type: 'button', class: 'btn secondary' }, 'Следующие импорты');
  let listOffset = 0, itemOffset = 0, selected = null, listGeneration = 0, detailGeneration = 0, uploaded = false, uploadBusy = false;
  let detailSummary = null, detailRange = null, detailNext = null, detailStatus = null;
  const counts = data => `Всего: ${data.total ?? '—'} · На проверке: ${data.pending ?? '—'} · Ошибки: ${data.invalid ?? '—'} · Одобрено: ${data.approved ?? '—'} · Отклонено: ${data.rejected ?? '—'}`;
  const failure = (target, error, reload) => {
    const conflict = error.status === 409 || error.status === 428;
    target.className = 'cm-notice is-error';
    target.replaceChildren(conflict ? 'Данные изменились. Обновите импорт и проверьте запись перед повторным действием.' : errorText(error));
    if (conflict) target.append(el('button', { type: 'button', class: 'btn secondary', onclick: reload }, 'Обновить импорт'));
    return conflict;
  };
  const loadList = async () => {
    const generation = ++listGeneration;
    listPrevious.disabled = listNext.disabled = true;
    try {
      const rows = await request('GET', `/api/testing/imports?limit=${listSize}&offset=${listOffset}`);
      if (generation !== listGeneration) return;
      list.replaceChildren(...rows.map(row => card(row.filename || 'Файл без названия', badge(row.status),
        el('p', { class: 'cm-muted' }, `${dateLabel(row.created_at)} · ${counts(row)}`),
        el('button', { type: 'button', class: 'btn secondary', onclick: async () => {
          await confirmReplace(() => { selected = row.id; itemOffset = 0; return fetchDetail(); });
        } }, 'Открыть импорт'))));
      if (!rows.length) list.append(empty('Импортов на этой странице нет.'));
      listMessage.className = 'cm-notice';
      listMessage.replaceChildren(`Импорты: ${listOffset + (rows.length ? 1 : 0)}–${listOffset + rows.length}${rows.length === listSize ? ' · Возможны следующие страницы' : ''}`);
      listPrevious.disabled = listOffset === 0;
      listNext.disabled = rows.length < listSize;
      return true;
    } catch (error) { if (generation === listGeneration) failure(listMessage, error, loadList); return false; }
  };
  const showErrors = errors => el('ul', { class: 'cm-list' }, ...errors.map(error => el('li', {},
    typeof error === 'string' ? error : JSON.stringify(error))));
  const drawItem = (item, container, generatedSource = false) => {
    const original = item.payload && typeof item.payload === 'object' && !Array.isArray(item.payload) ? item.payload : {};
    let current = item.edited_payload && typeof item.edited_payload === 'object' && !Array.isArray(item.edited_payload)
      ? item.edited_payload : original;
    const data = JSON.parse(JSON.stringify(current));
    const questionKey = Object.hasOwn(data, 'body') && !Object.hasOwn(data, 'question') ? 'body' : 'question';
    const external = input(128); external.required = false; external.value = data.external_id || item.external_id || '';
    const question = el('textarea', { rows: 3, maxlength: 5000 }); question.value = data[questionKey] || '';
    const code = input(200); code.required = false; code.value = data.competency_code || data.code || '';
    const codeSuggestions = el('datalist', { id: `import-codes-${String(item.id).replace(/[^a-zA-Z0-9_-]/g, '')}` },
      ...competencies.filter(c => c.code).map(c => el('option', { value: c.code }, c.name || c.code)));
    code.setAttribute('list', codeSuggestions.getAttribute('id'));
    const type = select([{ id: 'single', name: 'Один ответ' }, { id: 'multiple', name: 'Несколько ответов' }]);
    type.value = data.type || '';
    const explanation = el('textarea', { rows: 2, maxlength: 5000 }); explanation.value = data.explanation || '';
    const source = input(200); source.required = false; source.value = data.source_page_id || '';
    const revision = input(200); revision.required = false; revision.value = data.source_revision_id || '';
    const confirmed = el('input', { type: 'checkbox' });
    const options = Array.isArray(data.options) ? data.options.map(o => o && typeof o === 'object' && !Array.isArray(o) ? { ...o } : {}) : [];
    const optionsBox = el('div', { class: 'cm-stack' });
    const drawOptions = () => {
      optionsBox.replaceChildren(...options.map((option, index) => {
        const textKey = Object.hasOwn(option, 'text') ? 'text' : Object.hasOwn(option, 'body') ? 'body' : 'text';
        const correctKey = Object.hasOwn(option, 'is_correct') ? 'is_correct' : Object.hasOwn(option, 'correct') ? 'correct' : 'is_correct';
        const optionId = input(128); optionId.required = false; optionId.value = option.id || '';
        const text = input(1000); text.required = false; text.value = option[textKey] || '';
        const correct = el('input', { type: 'checkbox' }); correct.checked = Boolean(option[correctKey]);
        optionId.addEventListener('input', () => { option.id = optionId.value; });
        text.addEventListener('input', () => { option[textKey] = text.value; });
        correct.addEventListener('change', () => { option[correctKey] = correct.checked; });
        return el('div', { class: 'cm-option-row' }, field('ID варианта', optionId), field(`Вариант ${index + 1}`, text),
          el('label', { class: 'cm-choice' }, correct, 'Верный'),
          el('button', { type: 'button', class: 'btn secondary', onclick: () => { options.splice(index, 1); drawOptions(); fieldChanged(); } }, 'Удалить вариант'));
      }));
    };
    drawOptions();
    const edited = () => {
      const clean = { ...data, external_id: external.value.trim(), [questionKey]: question.value.trim(),
        competency_code: code.value.trim(), type: type.value,
        options: options.map(option => {
          const result = { ...option };
          if (Object.hasOwn(result, 'text')) delete result.body;
          if (Object.hasOwn(result, 'is_correct')) delete result.correct;
          return result;
        }), explanation: explanation.value.trim(), source_page_id: source.value.trim() || null,
        source_revision_id: revision.value.trim() || null };
      delete clean.code;
      if (questionKey === 'question') delete clean.body;
      return clean;
    };
    const initialForm = JSON.stringify(edited());
    const errors = Array.isArray(item.validation_errors) ? item.validation_errors : [];
    const message = notice();
    const save = el('button', { type: 'submit', class: 'btn secondary' }, 'Сохранить правки');
    const approve = el('button', { type: 'button', class: 'btn' }, 'Одобрить запись');
    const reject = el('button', { type: 'button', class: 'btn secondary' }, 'Отклонить запись');
    const reason = el('textarea', { rows: 2, 'aria-label': 'Причина отклонения' });
    const fields = el('form', { class: 'cm-form' }, field('Внешний ID записи', external), field('Вопрос', question), field('Код компетенции активной карты', code), codeSuggestions,
      field('Тип вопроса', type), optionsBox,
      el('button', { type: 'button', class: 'btn secondary', onclick: () => {
        if (options.length >= 20) { message.replaceChildren('Не более 20 вариантов ответа.'); return; }
        let index = 1;
        while (options.some(option => option.id === `option-${index}`)) index++;
        options.push({ id: `option-${index}`, text: '', is_correct: false }); drawOptions(); fieldChanged();
      } }, 'Добавить вариант'),
      field('Пояснение', explanation), field('Статья-источник (source_page_id)', source),
      field('Версия статьи (source_revision_id)', revision),
      el('label', { class: 'cm-choice' }, confirmed, generatedSource
        ? 'Подтверждаю, что лично сверил(а) вопрос с выбранной статьёй (LLM не подключена)'
        : 'Подтверждаю источник без указанной версии'),
      el('div', { class: 'cm-actions' }, save, approve), field('Причина отклонения (обязательно)', reason), reject, message);
    const raw = el('pre', { class: 'cm-import-json' }, JSON.stringify(original, null, 2));
    const revised = el('pre', { class: 'cm-import-json' }, JSON.stringify(current, null, 2));
    const diff = el('div', { class: 'cm-import-diff' }, el('div', {}, el('h5', {}, 'Исходная запись'), raw),
      el('div', {}, el('h5', {}, 'Сохранённые правки'), revised));
    let busy = false, stale = false, dirty = false;
    visibleEdits.set(container, { hasUnsavedChanges: () => dirty });
    const updateButtons = () => {
      save.disabled = reject.disabled = busy || stale;
      approve.disabled = busy || stale || errors.length > 0 || JSON.stringify(edited()) !== initialForm;
    };
    const fieldChanged = () => { dirty = true; updateButtons(); };
    // Explicit source confirmation is a review decision, not an edited question payload.
    fields.addEventListener('input', event => { if (event.target !== confirmed) fieldChanged(); });
    fields.addEventListener('change', event => { if (event.target !== confirmed) fieldChanged(); });
    const run = async (method, url, body) => {
      if (busy || stale) return;
      busy = true; updateButtons();
      try {
        const result = await request(method, url, body);
        current = result.edited_payload && typeof result.edited_payload === 'object' ? result.edited_payload : edited();
        Object.assign(item, result);
        // Replace only this row; other in-progress edits and all test builders stay mounted.
        drawItem(item, container, generatedSource);
        try {
          await loadList();
          const latest = await request('GET', `/api/testing/imports/${encodeURIComponent(selected)}?limit=${itemSize}&offset=${itemOffset}`);
          if (detailStatus) detailStatus.replaceChildren(badge(latest.status));
          if (detailSummary) detailSummary.replaceChildren(counts(latest));
          if (detailRange) detailRange.replaceChildren(`Записи: ${itemOffset + (latest.items?.length ? 1 : 0)}–${itemOffset + (latest.items?.length || 0)} из ${latest.total}`);
          if (detailNext) detailNext.disabled = itemOffset + (latest.items?.length || 0) >= latest.total;
        } catch (_) {
          const persistent = notice('Решение сохранено, но обновить счётчики не удалось. Обновите импорт.', true);
          container.append(persistent);
        }
        if (method === 'POST' && body.decision === 'approve') {
          try { await onApproved(); } catch (_) { /* Import decision has already been recorded. */ }
        }
      } catch (error) {
        stale = failure(message, error, loadDetail);
      } finally { busy = false; updateButtons(); }
    };
    const url = `/api/testing/imports/${encodeURIComponent(selected)}/items/${encodeURIComponent(item.id)}`;
    fields.addEventListener('submit', async event => {
      event.preventDefault();
      if (save.disabled) return;
      await run('PUT', url, { expected_token: item.expected_token, edited_payload: edited() });
    });
    approve.addEventListener('click', async () => {
      if (approve.disabled) return;
      if (source.value.trim() && (generatedSource || !revision.value.trim()) && !confirmed.checked) {
        message.className = 'cm-notice is-error';
        message.replaceChildren(generatedSource ? 'Сверьте вопрос со статьёй и подтвердите источник вручную перед одобрением.'
          : 'Подтвердите источник без версии перед одобрением.'); return;
      }
      await run('POST', `${url}/review`, { expected_token: item.expected_token, decision: 'approve',
        ...(source.value.trim() && confirmed.checked ? { source_confirmed: true } : {}) });
    });
    reject.addEventListener('click', async () => {
      if (reject.disabled) return;
      if (!reason.value.trim()) { message.className = 'cm-notice is-error'; message.replaceChildren('Укажите причину отклонения.'); return; }
      await run('POST', `${url}/review`, { expected_token: item.expected_token, decision: 'reject', reason: reason.value.trim() });
    });
    updateButtons();
    if (!['pending', 'invalid'].includes(item.status)) visibleEdits.delete(container);
    container.replaceChildren(card(`Запись ${item.ordinal ?? '—'}${item.external_id ? ` · ${item.external_id}` : ''}`,
      badge(item.status), ...(item.rejection_reason ? [notice(`Причина отклонения: ${item.rejection_reason}`, true)] : []),
      ...(errors.length ? [el('h5', {}, 'Ошибки проверки'), showErrors(errors)] : []),
      ...(item.reviewed_at ? [el('p', { class: 'cm-muted' }, `Рассмотрено: ${dateLabel(item.reviewed_at)}`)] : []),
      diff, ...(['pending', 'invalid'].includes(item.status) ? [fields] : [])));
  };
  const loadDetail = () => confirmReplace(() => fetchDetail());
  const fetchDetail = async () => {
    const generation = ++detailGeneration, id = selected, offset = itemOffset;
    detail.replaceChildren(notice('Загрузка записей…'));
    visibleEdits.clear();
    try {
      const data = await request('GET', `/api/testing/imports/${encodeURIComponent(id)}?limit=${itemSize}&offset=${offset}`);
      if (generation !== detailGeneration || id !== selected) return;
      const generatedSource = data.payload?.kind === 'generated_response_v1' && Boolean(data.payload.source);
      const rows = el('div', { class: 'cm-stack' });
      for (const item of data.items || []) { const row = el('div'); rows.append(row); drawItem(item, row, generatedSource); }
      const prev = el('button', { type: 'button', class: 'btn secondary' }, 'Предыдущие записи');
      const next = el('button', { type: 'button', class: 'btn secondary' }, 'Следующие записи');
      detailSummary = el('p', { class: 'cm-muted' }, counts(data));
      detailRange = el('p', { class: 'cm-muted' }, `Записи: ${offset + ((data.items || []).length ? 1 : 0)}–${offset + (data.items || []).length} из ${data.total}`);
      detailStatus = el('span', {}, badge(data.status));
      detailNext = next;
      prev.disabled = offset === 0; next.disabled = offset + (data.items || []).length >= data.total;
      prev.addEventListener('click', async () => { await confirmReplace(() => { itemOffset = Math.max(0, itemOffset - itemSize); return fetchDetail(); }); });
      next.addEventListener('click', async () => { await confirmReplace(() => { itemOffset += itemSize; return fetchDetail(); }); });
      detail.replaceChildren(card(data.filename || 'Импорт', detailStatus,
        ...(generatedSource ? [el('span', { class: 'cm-source-badge' }, 'Источник выбран вручную, связь с LLM ещё не подтверждена')] : []),
        detailSummary, detailRange,
        el('div', { class: 'cm-actions' }, prev, next), rows,
        el('button', { type: 'button', class: 'btn secondary', onclick: loadDetail }, 'Обновить импорт')));
      return true;
    } catch (error) { if (generation === detailGeneration) detail.replaceChildren(notice(errorText(error), true),
      el('button', { type: 'button', class: 'btn secondary', onclick: loadDetail }, 'Повторить загрузку импорта')); return false; }
  };
  listPrevious.addEventListener('click', async () => { listOffset = Math.max(0, listOffset - listSize); await loadList(); });
  listNext.addEventListener('click', async () => { listOffset += listSize; await loadList(); });
  upload.addEventListener('submit', async event => {
    event.preventDefault();
    if (uploadBusy || uploaded || uploadButton.disabled) return;
    await confirmReplace(async () => {
      const file = fileInput.files?.[0];
      if (!file) { uploadMessage.replaceChildren('Выберите файл JSON.'); return; }
      if (!file.name.toLowerCase().endsWith('.json') || file.size > maxBytes || !file.size) {
        uploadMessage.className = 'cm-notice is-error';
        uploadMessage.replaceChildren('Нужен непустой файл .json размером не более 512 КиБ.'); return;
      }
      uploadBusy = true; uploadButton.disabled = true;
      try {
        const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
        JSON.parse(text);
        const result = await request('POST', '/api/testing/imports', { filename: file.name, json_text: text });
        uploaded = true; uploadMessage.className = 'cm-notice';
        uploadMessage.replaceChildren('Файл загружен. Для нового файла выберите его заново.');
        selected = result.id; itemOffset = listOffset = 0;
        if ((await Promise.all([loadList(), fetchDetail()])).includes(false)) {
          uploadMessage.className = 'cm-notice is-error';
          uploadMessage.replaceChildren('Импорт создан, но список или записи не загрузились. Обновите импорт; повторно отправлять файл не нужно.',
            el('button', { type: 'button', class: 'btn secondary', onclick: async () => {
              if ((await Promise.all([loadList(), loadDetail()])).every(Boolean)) {
                uploadMessage.className = 'cm-notice'; uploadMessage.replaceChildren('Импорт загружен.');
              }
            } }, 'Обновить импорт'));
        }
      } catch (error) {
        uploadMessage.className = 'cm-notice is-error';
        uploadMessage.replaceChildren(uploaded ? 'Импорт создан, но не удалось обновить список. Обновите импорт; не отправляйте файл повторно.' :
          error instanceof SyntaxError || error.name === 'TypeError' ? 'Файл должен содержать корректный JSON в UTF-8.' : errorText(error));
        if (uploaded) uploadMessage.append(el('button', { type: 'button', class: 'btn secondary', onclick: async () => {
          await Promise.all([loadList(), loadDetail()]);
        } }, 'Обновить импорт'));
      } finally { uploadBusy = false; uploadButton.disabled = uploaded; }
    });
  });
  fileInput.addEventListener('change', () => {
    if (uploadBusy) return;
    uploaded = false; uploadButton.disabled = false; uploadMessage.replaceChildren();
  });
  // Interim workflow: only the operator supplies the JSON. The browser never contacts an LLM.
  const sourceSearch = input(200); sourceSearch.required = false; sourceSearch.placeholder = 'Название статьи';
  const sourceList = el('div', { class: 'cm-source-list' });
  const sourceMessage = notice();
  const sourcePrevious = el('button', { type: 'button', class: 'btn secondary' }, 'Предыдущие статьи');
  const sourceNext = el('button', { type: 'button', class: 'btn secondary' }, 'Следующие статьи');
  const selectedSource = el('p', { class: 'cm-muted' }, 'Статья не выбрана.');
  let sourceOffset = 0, sourceGeneration = 0, sourcePage = null;
  const loadSources = async () => {
    const generation = ++sourceGeneration;
    sourcePrevious.disabled = sourceNext.disabled = true;
    try {
      const query = new URLSearchParams({ q: sourceSearch.value.trim(), limit: '20', offset: String(sourceOffset) });
      const data = await request('GET', `/api/testing/imports/source-pages?${query}`);
      if (generation !== sourceGeneration) return;
      const items = Array.isArray(data.items) ? data.items : [];
      sourceList.replaceChildren(...items.map(page => el('button', { type: 'button', class: 'btn secondary cm-source-choice', onclick: () => {
        sourcePage = { id: page.id, current_revision_id: page.current_revision_id };
        if (!generatedBusy) { generatedCreated = false; submitGenerated.disabled = false; }
        selectedSource.replaceChildren(`Выбрана статья: ${page.title || 'Без названия'} · ${page.space_name || 'Пространство не указано'}. Версия зафиксирована при выборе.`,
          el('span', { class: 'cm-source-badge' }, 'Источник выбран вручную, связь с LLM ещё не подтверждена'));
      } }, `${page.title || 'Без названия'} · ${page.space_name || 'Пространство не указано'}`)));
      if (!items.length) sourceList.append(empty('Доступных статей нет. Уточните поиск.'));
      sourceMessage.className = 'cm-notice';
      sourceMessage.replaceChildren(`Статьи: ${sourceOffset + (items.length ? 1 : 0)}–${sourceOffset + items.length} из ${data.total ?? 0}`);
      sourcePrevious.disabled = sourceOffset === 0;
      sourceNext.disabled = sourceOffset + items.length >= data.total;
    } catch (error) {
      if (generation === sourceGeneration) { sourceMessage.className = 'cm-notice is-error'; sourceMessage.replaceChildren(errorText(error)); }
    }
  };
  sourceSearch.addEventListener('input', () => {
    ++sourceGeneration; sourcePage = null;
    sourceList.replaceChildren(); sourcePrevious.disabled = sourceNext.disabled = true;
    sourceMessage.replaceChildren('Нажмите «Найти статьи» для поиска.');
    selectedSource.replaceChildren('Статья не выбрана.');
  });
  const searchSources = el('div', { class: 'cm-form' }, field('Поиск статьи по названию', sourceSearch),
    el('div', { class: 'cm-actions' }, el('button', { type: 'button', class: 'btn secondary', onclick: async () => {
      sourceOffset = 0; await loadSources();
    } }, 'Найти статьи')));
  sourcePrevious.addEventListener('click', async () => { if (!sourcePrevious.disabled) { sourceOffset -= 20; await loadSources(); } });
  sourceNext.addEventListener('click', async () => { if (!sourceNext.disabled) { sourceOffset += 20; await loadSources(); } });
  const countInput = el('input', { type: 'number', required: '', min: 1, max: 30, value: '5' });
  const answersInput = el('input', { type: 'number', required: '', min: 2, max: 8, value: '4' });
  const topicsInput = el('textarea', { rows: 3, placeholder: 'Одна тема на строку, не более 10 тем' });
  const responseInput = el('textarea', { rows: 7, 'aria-label': 'JSON-ответ с вопросами',
    placeholder: '{"questions":[{"question":"...","options":["..."],"correct_answer":0}]}' });
  const responseFile = el('input', { type: 'file', accept: '.json,application/json', 'aria-label': 'Файл JSON-ответа (необязательно)' });
  const generatedMessage = notice(), countWarning = notice();
  const generate = el('button', { class: 'btn secondary', type: 'button', disabled: '',
    title: 'Генерация через LLM пока не подключена' }, 'Сгенерировать');
  generate.disabled = true;
  const submitGenerated = el('button', { class: 'btn', type: 'submit' }, 'Импортировать JSON-ответ');
  const generated = el('form', { class: 'cm-form cm-generated-import' },
    el('p', { class: 'cm-notice' }, 'Генерация через LLM пока не подключена: выберите статью и вставьте JSON-ответ вручную'),
    searchSources, sourceList, el('div', { class: 'cm-actions' }, sourcePrevious, sourceNext), sourceMessage, selectedSource,
    field('Количество вопросов (1–30)', countInput), field('Количество вариантов ответа (2–8)', answersInput),
    field('Темы (не более 10, по одной на строку)', topicsInput),
    field('JSON-ответ (вставьте вручную)', responseInput), field('Или загрузите файл ответа .json (до 512 КиБ)', responseFile),
    el('div', { class: 'cm-actions' }, generate, submitGenerated), countWarning, generatedMessage);
  let generatedBusy = false, generatedCreated = false;
  generated.addEventListener('submit', async event => {
    event.preventDefault();
    if (generatedBusy || generatedCreated) return;
    generatedMessage.className = 'cm-notice is-error';
    if (!sourcePage?.id || !sourcePage.current_revision_id) {
      generatedMessage.replaceChildren('Выберите статью с текущей версией из списка.'); return;
    }
    const questionsCount = Number(countInput.value), answersCount = Number(answersInput.value);
    const topics = topicsInput.value.split(/\r?\n/).map(t => t.trim()).filter(Boolean);
    if (!Number.isInteger(questionsCount) || questionsCount < 1 || questionsCount > 30 ||
      !Number.isInteger(answersCount) || answersCount < 2 || answersCount > 8 || topics.length > 10) {
      generatedMessage.replaceChildren('Проверьте параметры: 1–30 вопросов, 2–8 ответов, не более 10 тем.'); return;
    }
    generatedBusy = true; submitGenerated.disabled = true;
    try {
      const file = responseFile.files?.[0];
      if (file && (!file.name.toLowerCase().endsWith('.json') || !file.size || file.size > maxBytes))
        throw new Error('Нужен непустой файл .json размером не более 512 КиБ.');
      const text = file ? new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()) : responseInput.value;
      if (!text.trim() || new TextEncoder().encode(text).length > maxBytes)
        throw new Error('Вставьте JSON-ответ размером не более 512 КиБ.');
      let response;
      try { response = JSON.parse(text); } catch (_) { throw new Error('JSON-ответ некорректен. Проверьте синтаксис.'); }
      if (!response || !Array.isArray(response.questions)) throw new Error('JSON-ответ должен содержать массив questions.');
      countWarning.replaceChildren(response.questions.length !== questionsCount
        ? `В ответе ${response.questions.length} вопросов, запрошено ${questionsCount}. Проверьте записи перед одобрением.` : '');
      const source = { ...sourcePage };
      await confirmReplace(async () => {
        generatedBusy = true; submitGenerated.disabled = true;
        try {
          const result = await request('POST', '/api/testing/imports/from-generated', {
            source_page_id: source.id, source_revision_id: source.current_revision_id,
            settings: { questions_count: questionsCount, answers_count: answersCount, topics }, response
          });
          generatedCreated = true;
          generatedMessage.className = 'cm-notice'; generatedMessage.replaceChildren('Импорт создан. Записи требуют ручной проверки.');
          selected = result.id; itemOffset = listOffset = 0;
          if ((await Promise.all([loadList(), fetchDetail()])).includes(false)) {
            generatedMessage.className = 'cm-notice is-error';
            generatedMessage.replaceChildren('Импорт создан, но записи не загрузились. Обновите импорт; не отправляйте JSON повторно.');
          }
        } catch (error) {
          generatedMessage.className = 'cm-notice is-error';
          generatedMessage.replaceChildren(errorText(error));
        } finally { generatedBusy = false; submitGenerated.disabled = generatedCreated; }
      });
    } catch (error) {
      generatedMessage.className = 'cm-notice is-error';
      generatedMessage.replaceChildren(error.name === 'TypeError' ? 'Файл должен содержать корректный JSON в UTF-8.' : errorText(error));
    } finally { generatedBusy = false; submitGenerated.disabled = generatedCreated; }
  });
  for (const control of [sourceSearch, countInput, answersInput, topicsInput, responseInput, responseFile])
    control.addEventListener('change', () => { if (!generatedBusy) { generatedCreated = false; submitGenerated.disabled = false; } });
  await loadList();
  await loadSources();
  const linkedImport = new URLSearchParams((typeof location === 'undefined' ? '' : location.hash).split('?')[1] || '').get('import');
  if (linkedImport && /^[0-9a-f-]{36}$/i.test(linkedImport)) {
    selected = linkedImport;
    await fetchDetail();
  }
  const result = card('Импорт вопросов из JSON', el('p', { class: 'cm-muted' },
    'Только для ответственного за КК. Файл UTF-8 до 512 КиБ; записи не попадут в банк без ручного одобрения.'),
  upload, el('h4', {}, 'JSON-ответ для выбранной статьи (без подключения к LLM)'), generated,
  el('h4', {}, 'История импортов'), list, el('div', { class: 'cm-actions' }, listPrevious, listNext), listMessage, navigationNotice, detail);
  result.hasUnsavedChanges = () => [...visibleEdits.values()].some(editor => editor.hasUnsavedChanges());
  return result;
}

function testMetadata(test) {
  const published = test.latest_published_version || test.version;
  return `${published ? `Опубликована версия ${published}` : 'Публикаций пока нет'}${test.has_draft ?
    ` · Черновик версии ${test.next_version || (Number(published || 0) + 1)}` : ''}`;
}

// Only safe published snapshots are used here, never the question-bank detail/answer key.
export async function renderTestVersions(testId) {
  if (!state.user.roles.some(r => ['kk_responsible', 'manager'].includes(r))) {
    return notice('Просмотр опубликованных версий доступен руководителю и ответственному за карту.', true);
  }
  const versions = await request('GET', `/api/testing/tests/${encodeURIComponent(testId)}/versions`);
  const root = card('Опубликованные версии', el('p', { class: 'cm-muted' }, 'Только просмотр. Опубликованные версии неизменяемы.'));
  const detail = el('div');
  for (const version of versions) root.append(card(`Версия ${version.version}`, el('p', { class: 'cm-muted' },
    `${dateLabel(version.published_at)} · Блоков: ${version.block_count} · Компетенций: ${version.competency_count} · Вопросов: ${version.question_count}`),
  actionForm(`Открыть версию ${version.version}`, [], async () => {
    const snapshot = await request('GET', `/api/testing/tests/${encodeURIComponent(testId)}/versions/${version.version}`);
    detail.replaceChildren(el('h3', {}, `${snapshot.test_title || 'Тест'} · версия ${snapshot.test_version}`),
      ...(snapshot.blocks || []).flatMap(block => {
        const rows = (block.competencies || []).filter(c => (c.questions || []).length);
        const competencyViews = rows.map(c => {
          const questions = c.questions.map(q => el('div', { class: 'cm-preview-question' },
            el('p', {}, q.body), el('p', { class: 'cm-muted' }, q.type === 'single' ? 'Один ответ' : 'Несколько ответов'),
            el('ul', {}, ...(q.options || []).map(o => el('li', {}, o.body)))));
          return el('section', {}, el('h4', {}, c.competency_name || 'Компетенция'), ...questions);
        });
        return rows.length ? [card(block.title || 'Блок', ...competencyViews)] : [];
      }));
  })));
  if (!versions.length) root.append(empty('Опубликованных версий пока нет.'));
  root.append(detail);
  return root;
}

// Each competency loads the entire approved set, not a shared truncated bank page.
async function approvedQuestions(competencyId) {
  const result = [];
  for (let offset = 0; ; offset += 100) {
    const query = new URLSearchParams({ competency_id: competencyId, status: 'approved', limit: '100', offset: String(offset) });
    const page = await request('GET', `/api/testing/questions?${query}`);
    result.push(...page);
    if (page.length < 100) return result;
  }
}

export async function renderTestBuilder(competencies, initialDraft = null, refresh, onSaved = () => {}) {
  if (!state.user.roles.includes('kk_responsible')) return notice('Черновики доступны только ответственному за карту компетенций.', true);
  const available = competencies.map(c => ({ id: c.competency_id, name: c.name || c.competency_name || 'Компетенция' }));
  const title = input(250);
  const description = el('textarea', { maxlength: 5000, rows: 2 });
  const titleNote = notice();
  const status = notice();
  const blocksBox = el('div', { class: 'cm-stack' });
  const counts = el('p', { class: 'cm-muted' });
  const versionNote = el('p', { class: 'cm-muted' });
  const save = el('button', { type: 'submit', class: 'btn' }, initialDraft ? 'Сохранить черновик' : 'Создать тест');
  const publish = el('button', { type: 'button', class: 'btn secondary' }, 'Опубликовать');
  let draft = initialDraft, blocks = [], savedSnapshot = null, baselineSnapshot = null, busy = false, stale = false;
  const newCompetency = (value = {}) => ({ competency_id: value.competency_id || '',
    question_ids: [...(value.question_ids || [])], approved: [], unavailable: new Map(), loading: false, error: '', generation: 0 });
  const newBlock = (value = {}) => {
    const blockTitle = input(250);
    blockTitle.value = value.title || '';
    return { title: blockTitle, competencies: value.competencies ? value.competencies.map(newCompetency) : [newCompetency(), newCompetency()] };
  };
  const totalCompetencies = () => blocks.reduce((sum, b) => sum + b.competencies.length, 0);
  const payload = () => ({ title: title.value.trim(), description: description.value.trim(), blocks: blocks.map(b => ({
    title: b.title.value.trim(), competencies: b.competencies.map(c => ({ competency_id: c.competency_id, question_ids: [...c.question_ids] }))
  })) });
  const message = text => { status.className = 'cm-notice is-error'; status.replaceChildren(text); };
  const move = (items, index, direction) => {
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    [items[index], items[target]] = [items[target], items[index]];
  };
  const control = (label, action, disabled = false) => {
    const button = el('button', { type: 'button', class: 'btn secondary', onclick: () => {
      if (!busy && !stale && !button.disabled) return action();
    } }, label);
    button.disabled = disabled;
    return button;
  };
  const drawQuestions = c => {
    const box = c.questionsBox;
    if (!box) return;
    if (!c.competency_id) { box.replaceChildren(empty('Сначала выберите компетенцию.')); return; }
    const selected = el('ol', { class: 'cm-selected-questions' });
    c.question_ids.forEach((id, index) => {
      const question = c.approved.find(q => q.id === id);
      const unavailable = c.unavailable.get(id);
      selected.append(el('li', {}, el('p', {}, question?.body || unavailable?.body || 'Ранее выбранный вопрос'),
        ...(!question && !c.loading ? [notice('Недоступен для публикации: вопрос больше не утверждён, неактивен или отсутствует. Удалите его из выбора.', true)] : []),
        el('div', { class: 'cm-actions' },
          control('Вопрос вверх', () => { move(c.question_ids, index, -1); drawQuestions(c); }, c.loading || index === 0),
          control('Вопрос вниз', () => { move(c.question_ids, index, 1); drawQuestions(c); }, c.loading || index === c.question_ids.length - 1),
          control('Убрать вопрос', () => { c.question_ids.splice(index, 1); drawQuestions(c); }, c.loading))));
    });
    const choices = el('div', { class: 'cm-question-choices' });
    for (const q of c.approved) {
      const checkbox = el('input', { type: 'checkbox', value: q.id });
      checkbox.checked = c.question_ids.includes(q.id);
      checkbox.disabled = c.loading;
      checkbox.addEventListener('change', () => {
        if (busy || stale || c.loading) return;
        if (checkbox.checked && !c.question_ids.includes(q.id)) {
          if (c.question_ids.length >= 50) { checkbox.checked = false; message('Не более 50 вопросов на компетенцию. Сначала уберите лишний вопрос.'); return; }
          c.question_ids.push(q.id);
        } else if (!checkbox.checked) c.question_ids = c.question_ids.filter(id => id !== q.id);
        drawQuestions(c);
      });
      choices.append(el('label', { class: 'cm-choice' }, checkbox, q.body));
    }
    box.replaceChildren(el('p', { class: 'cm-muted' }, `Выбрано вопросов: ${c.question_ids.length}/50. В черновике можно 0, для публикации — минимум 2.`),
      ...(c.loading ? [notice('Загрузка утверждённых вопросов… Выбор и порядок сохранены.')] : []),
      ...(c.error ? [notice(c.error, true), control('Повторить загрузку вопросов', () => loadQuestions(c))] : []),
      el('h5', {}, 'Выбранные вопросы — порядок в тесте'), selected,
      el('h5', {}, 'Утверждённые вопросы — отметьте нужные'), c.approved.length ? choices : empty('Утверждённых вопросов нет.'));
  };
  const loadQuestions = async c => {
    const generation = ++c.generation;
    const id = c.competency_id;
    c.loading = true; c.error = '';
    drawQuestions(c);
    try {
      const approved = (id ? await approvedQuestions(id) : []).filter(q => q.is_active !== false && q.is_active !== 0 &&
        (!q.moderation_status || q.moderation_status === 'approved'));
      if (generation !== c.generation) return;
      c.approved = approved;
      await Promise.all(c.question_ids.filter(qid => !approved.some(q => q.id === qid)).map(async qid => {
        try {
          const detail = await request('GET', `/api/testing/questions/${encodeURIComponent(qid)}`);
          // Intentionally do not retain or render answer keys in the builder.
          if (generation === c.generation) c.unavailable.set(qid, { body: detail.body });
        } catch (_) { /* Retain the selection with an explicit unavailable explanation. */ }
      }));
    } catch (error) { if (generation === c.generation) c.error = errorText(error); }
    finally { if (generation === c.generation) { c.loading = false; drawQuestions(c); } }
  };
  const updateChoices = () => {
    const selected = new Set(blocks.flatMap(b => b.competencies.map(c => c.competency_id)).filter(Boolean));
    for (const block of blocks) for (const c of block.competencies) {
      for (const option of c.select.children) option.disabled = Boolean(option.value && option.value !== c.competency_id && selected.has(option.value));
    }
  };
  const draw = () => {
    blocksBox.replaceChildren();
    counts.replaceChildren(`Блоков: ${blocks.length}/10 · Компетенций: ${totalCompetencies()}/20`);
    blocks.forEach((block, blockIndex) => {
      const rows = el('div', { class: 'cm-stack' });
      block.competencies.forEach((c, index) => {
        const options = [...available];
        const inactive = c.competency_id && !available.some(a => a.id === c.competency_id);
        if (inactive) options.push({ id: c.competency_id, name: 'Ранее выбранная компетенция вне активной карты' });
        c.select = select(options, 'Выберите компетенцию активной карты');
        c.select.value = c.competency_id;
        c.questionsBox = el('div');
        c.select.addEventListener('change', async () => {
          const next = c.select.value;
          if (busy || stale || blocks.some(b => b.competencies.some(other => other !== c && other.competency_id && other.competency_id === next))) {
            c.select.value = c.competency_id; message('Компетенция может встречаться в тесте только один раз.'); return;
          }
          c.competency_id = next; c.question_ids = []; c.approved = []; c.unavailable = new Map();
          draw();
          await loadQuestions(c);
        });
        rows.append(el('fieldset', { class: 'cm-test-competency' }, el('legend', {}, `Компетенция ${index + 1}`),
          field('Компетенция активной карты', c.select),
          ...(inactive ? [notice('Эта компетенция больше не входит в активную карту. Для публикации замените или удалите её.', true)] : []),
          el('div', { class: 'cm-actions' },
            control('Компетенция вверх', () => { move(block.competencies, index, -1); draw(); }, index === 0),
            control('Компетенция вниз', () => { move(block.competencies, index, 1); draw(); }, index === block.competencies.length - 1),
            control('Удалить компетенцию', () => { block.competencies.splice(index, 1); draw(); }, block.competencies.length <= 2)), c.questionsBox));
        drawQuestions(c);
      });
      blocksBox.append(el('fieldset', { class: 'cm-test-block' }, el('legend', {}, `Блок ${blockIndex + 1}`),
        field('Название блока', block.title), el('div', { class: 'cm-actions' },
          control('Блок вверх', () => { move(blocks, blockIndex, -1); draw(); }, blockIndex === 0),
          control('Блок вниз', () => { move(blocks, blockIndex, 1); draw(); }, blockIndex === blocks.length - 1),
          control('Удалить блок', () => { blocks.splice(blockIndex, 1); draw(); }, blocks.length <= 1)), rows,
        control('Добавить компетенцию', () => {
          if (totalCompetencies() >= 20) { message('Не более 20 компетенций во всём тесте.'); return; }
          block.competencies.push(newCompetency()); draw();
        })));
    });
    updateChoices();
  };
  const addBlock = control('Добавить блок', () => {
    if (blocks.length >= 10) { message('В тесте может быть от 1 до 10 блоков.'); return; }
    if (totalCompetencies() + 2 > 20) { message('Новый блок требует минимум две компетенции. Всего в тесте — не более 20.'); return; }
    blocks.push(newBlock()); draw();
  });
  const content = el('fieldset', { class: 'cm-builder-fields' }, el('legend', {}, 'Структура теста'),
    field('Название теста', title), titleNote, field('Описание теста', description),
    el('p', { class: 'cm-muted' }, 'От 1 до 10 блоков, минимум 2 компетенции в каждом, всего не более 20. Компетенции не повторяются. Выбирайте вопросы явно: 0–50 в черновике, 2–50 при публикации. Порядок задаётся кнопками.'),
    counts, blocksBox, addBlock);
  const form = el('form', { class: 'cm-form cm-test-builder' }, versionNote, content,
    el('div', { class: 'cm-actions' }, save, publish), status);
  // Local controllers deliberately update only question choices, never adopt a server draft.
  form.refreshQuestionChoices = () => Promise.all(blocks.flatMap(b => b.competencies).filter(c => c.competency_id).map(loadQuestions));
  form.hasUnsavedChanges = () => (busy && !stale) || JSON.stringify(payload()) !== baselineSnapshot;
  const adopt = async value => {
    draft = value;
    title.value = value?.title || '';
    description.value = value?.description || '';
    title.disabled = Boolean(value?.title_locked || value?.latest_published_version);
    titleNote.replaceChildren(title.disabled ? 'После первой публикации название теста изменить нельзя.' : 'Название будет зафиксировано при первой публикации.');
    versionNote.replaceChildren(value ? testMetadata({ ...value, has_draft: true }) : 'Новый черновик');
    blocks = value ? value.blocks.map(newBlock) : [newBlock()];
    baselineSnapshot = JSON.stringify(payload());
    savedSnapshot = value ? baselineSnapshot : null;
    save.replaceChildren(value ? 'Сохранить черновик' : 'Создать тест');
    draw();
    await Promise.all(blocks.flatMap(b => b.competencies).filter(c => c.competency_id).map(loadQuestions));
  };
  const validate = publishing => {
    const data = payload();
    if (!data.title) throw new Error('Укажите название теста.');
    if (!data.blocks.length || data.blocks.length > 10) throw new Error('В тесте должно быть от 1 до 10 блоков.');
    if (totalCompetencies() > 20) throw new Error('В тесте должно быть не более 20 компетенций.');
    const seen = new Set();
    for (const block of blocks) {
      if (!block.title.value.trim()) throw new Error('Укажите название каждого блока.');
      if (block.competencies.length < 2) throw new Error('В каждом блоке нужны минимум две компетенции.');
      for (const c of block.competencies) {
        if (!c.competency_id) throw new Error('Выберите компетенцию в каждой строке.');
        if (seen.has(c.competency_id)) throw new Error('Компетенция может встречаться в тесте только один раз.');
        seen.add(c.competency_id);
        if (c.question_ids.length > 50) throw new Error('Не более 50 вопросов на компетенцию.');
        if (publishing) {
          if (c.loading || c.error) throw new Error('Дождитесь успешной загрузки вопросов всех компетенций.');
          if (!available.some(a => a.id === c.competency_id)) throw new Error('Для публикации замените компетенции вне активной карты.');
          if (c.question_ids.length < 2) throw new Error('Для каждой компетенции выберите минимум два утверждённых вопроса.');
          if (c.question_ids.some(id => !c.approved.some(q => q.id === id))) throw new Error('Уберите недоступные вопросы перед публикацией.');
        }
      }
    }
    return data;
  };
  const run = async action => {
    if (busy || stale || !form.reportValidity()) return;
    busy = true; save.disabled = publish.disabled = content.disabled = true;
    status.className = 'cm-notice'; status.replaceChildren('Сохранение…');
    try { await action(); }
    catch (error) {
      stale = [409, 428].includes(error.status) || Boolean(draft?.id && draft.expected_token == null);
      message(error.status === 409 ? 'Данные изменились. Обновите их и проверьте перед повторным действием. Несохранённые изменения будут заменены.' : errorText(error));
      if (stale) {
        const reload = el('button', { type: 'button', class: 'btn secondary', onclick: async () => {
          if (busy) return;
          busy = true;
          try {
            if (draft?.id) {
              await adopt(await request('GET', `/api/testing/tests/${encodeURIComponent(draft.id)}/draft`));
              onSaved(draft);
            } else if (refresh) {
              // The conflict notice explicitly warns about replacing this editor, not other open drafts.
              await refresh('Данные обновлены.', form); return;
            }
            stale = false;
            status.replaceChildren('Данные обновлены. Проверьте структуру перед сохранением.');
          } catch (loadError) { message(errorText(loadError)); status.append(reload); }
          finally { busy = false; save.disabled = publish.disabled = content.disabled = stale; }
        } }, 'Обновить данные');
        status.append(reload);
      }
    } finally { busy = false; save.disabled = publish.disabled = content.disabled = stale; }
  };
  form.addEventListener('submit', async event => {
    event.preventDefault();
    await run(async () => {
      const data = validate(false);
      let saved;
      if (draft?.id) saved = await request('PUT', `/api/testing/tests/${encodeURIComponent(draft.id)}/draft`, { ...data, expected_token: draft.expected_token });
      else {
        const created = await request('POST', '/api/testing/tests', data);
        // Remember the ID before GET so a failed read cannot create a second test on retry.
        draft = { ...data, id: created.id };
        saved = await request('GET', `/api/testing/tests/${encodeURIComponent(created.id)}/draft`);
      }
      await adopt(saved);
      onSaved(saved);
      status.replaceChildren('Черновик сохранён. Публикация — отдельное действие.');
    });
  });
  publish.addEventListener('click', async () => {
    await run(async () => {
      const data = validate(true);
      if (!draft?.id || JSON.stringify(data) !== savedSnapshot) throw new Error('Сначала сохраните изменения черновика, затем опубликуйте сохранённую версию.');
      const result = await request('POST', `/api/testing/tests/${encodeURIComponent(draft.id)}/publish`, { expected_token: draft.expected_token });
      status.replaceChildren(`Опубликована версия ${result.version}.`);
      stale = true; // Published snapshots cannot be modified through this editor.
      if (refresh) {
        try { await refresh(`Опубликована версия ${result.version}.`); }
        catch (_) { status.replaceChildren(`Версия ${result.version} опубликована. Не удалось обновить список — обновите страницу.`); }
      }
    });
  });
  await adopt(initialDraft);
  return form;
}
