import { el, state } from '../app.js';
import { renderTesting, renderManagerTesting, renderResponsibleTesting, request, actionForm,
  badge, card, empty, field, input, select, notice, errorText } from './testing.js';

async function employeeArea() {
  const mine = await request('GET', '/api/competencies/mine');
  const levels = el('div', { class: 'levels-grid' });
  for (const competency of mine) levels.append(card(competency.name,
    el('div', { class: 'level-number' }, competency.current_level == null ? 'Не оценено' : competency.current_level),
    el('p', { class: 'cm-muted' }, `Текущий уровень · цель ${competency.target_level}`),
    el('div', { class: 'cm-levels', 'aria-hidden': 'true' }, ...[0, 1, 2, 3].map(level =>
      el('span', { class: competency.current_level != null && level <= competency.current_level ? 'is-filled' : '' }, level)))));
  return el('div', { class: 'cm-stack' }, el('h2', {}, 'Моя карта развития'),
    mine.length ? levels : empty('Активной карты подразделения пока нет. После утверждения здесь появятся компетенции и целевые уровни.'),
    await renderTesting({ embedded: true }));
}

function profileCard(profile, { responsible, manager, active, catalog, refresh }) {
  const box = card(profile.name, el('div', { class: 'cm-actions' }, badge(profile.status),
    el('span', { class: 'cm-muted' }, `Версия ${profile.version}${profile.is_active ? ' · Активная карта' : ''}`)),
    profile.review_note ? notice(`Комментарий руководителя: ${profile.review_note}`) : null,
    profile.competencies.length ? el('ul', { class: 'cm-list' }, ...profile.competencies.map(c =>
      el('li', {}, el('strong', {}, c.name), ` — целевой уровень ${c.target_level}`))) : empty('В карте ещё нет компетенций.'));
  if (responsible && ['draft', 'returned'].includes(profile.status)) {
    const competency = select(catalog.competencies, 'Выберите компетенцию из каталога');
    const target = select([0, 1, 2, 3].map(n => ({ id: n, name: `Уровень ${n}` })));
    target.value = '1';
    competency.addEventListener('change', () => {
      const existing = profile.competencies.find(c => c.competency_id === competency.value);
      if (existing) target.value = String(existing.target_level);
    });
    box.append(actionForm('Сохранить компетенцию', [field('Компетенция', competency), field('Целевой уровень', target)],
      () => request('PUT', `/api/competencies/profiles/${profile.id}/competencies`,
        { competency_id: competency.value, target_level: Number(target.value) }), refresh),
    actionForm('На утверждение', [], () => request('POST', `/api/competencies/profiles/${profile.id}/submit`, {}), refresh));
  }
  if (manager && profile.status === 'pending_approval') {
    box.append(actionForm('Утвердить карту', [], () => request('POST', `/api/competencies/profiles/${profile.id}/review`,
      { decision: 'approve', expected_active_id: active.id, expected_active_version: active.version }), refresh));
    const note = el('textarea', { rows: 2, required: '', maxlength: 2000 });
    box.append(actionForm('Вернуть на доработку', [field('Причина возврата', note)], async () => {
      if (!note.value.trim()) throw new Error('Укажите причину возврата.');
      await request('POST', `/api/competencies/profiles/${profile.id}/review`, { decision: 'return', note: note.value.trim() });
    }, refresh));
  }
  return box;
}

function summaryTable(rows) {
  if (!rows.length) return empty('Нет активной карты или сотрудников подразделения.');
  return el('div', { class: 'cm-table-wrap' }, el('table', {},
    el('thead', {}, el('tr', {}, ...['Сотрудник', 'Компетенция', 'Текущий уровень', 'Цель'].map(text => el('th', { scope: 'col' }, text)))),
    el('tbody', {}, ...rows.map(row => el('tr', {},
      el('td', {}, row.display_name || 'Сотрудник'), el('td', {}, row.competency_name || 'Компетенция'),
      el('td', {}, row.current_level == null ? 'Не оценено' : row.current_level), el('td', {}, row.target_level))))));
}

async function departmentArea(kind, refresh) {
  const departments = await request('GET', '/api/competencies/departments');
  const manager = kind === 'manager';
  const available = departments.filter(d => manager ? d.manager_id === state.user.id : d.responsible);
  if (!available.length) return empty(manager ? 'Вы пока не назначены руководителем подразделения.' : 'Вы пока не назначены ответственным за карту компетенций подразделения.');
  const catalog = manager ? null : await request('GET', '/api/competencies/catalog');
  const root = el('div', { class: 'cm-stack' });
  const editors = [];
  for (const department of available) {
    const profiles = await request('GET', `/api/competencies/departments/${department.id}/profiles`);
    const active = manager ? await request('GET', `/api/competencies/departments/${department.id}/active`) : null;
    const section = el('section', { class: 'cm-stack' }, el('div', { class: 'section-heading' },
      el('h2', {}, department.name), el('span', { class: 'status-badge' }, manager ? 'Руководитель' : 'Ответственный за КК')));
    if (!manager) {
      const name = input();
      section.append(card('Новый проект карты', actionForm('Создать проект', [field('Название карты', name)],
        () => request('POST', `/api/competencies/departments/${department.id}/profiles`, { name: name.value.trim() }), refresh)));
    }
    section.append(el('h3', {}, manager ? 'Карты и согласование' : 'Проекты карт'),
      profiles.length ? el('div', { class: 'evaluation-grid' }, ...profiles.map(profile => profileCard(profile,
        { responsible: !manager, manager, active, catalog, refresh }))) : empty('Карты пока не созданы.'));
    if (manager) {
      const rows = await request('GET', `/api/competencies/departments/${department.id}/summary`);
      section.append(card('Сводка подразделения', summaryTable(rows)));
    }
    // A failed test API must not hide maps or the department summary.
    try {
      const testing = manager ? await renderManagerTesting(department, refresh) :
        await renderResponsibleTesting(profiles.find(p => p.is_active)?.competencies || [], refresh);
      if (!manager) editors.push(testing);
      section.append(testing);
    } catch (error) { section.append(notice(errorText(error), true)); }
    root.append(section);
  }
  root.hasUnsavedChanges = () => editors.some(editor => editor.hasUnsavedChanges?.());
  return root;
}

async function adminArea(refresh) {
  const [catalog, users, departments] = await Promise.all([
    request('GET', '/api/competencies/catalog'), request('GET', '/api/admin/users'), request('GET', '/api/admin/departments')
  ]);
  const categoryCode = input(50), categoryName = input();
  const category = select(catalog.categories, 'Выберите категорию');
  const code = input(80), name = input(255);
  const departmentCode = input(50), departmentName = input();
  const root = el('div', { class: 'cm-stack' }, card('Подразделения и назначения',
    el('p', { class: 'cm-muted' }, 'Для назначения сотрудник должен состоять в отделе и иметь соответствующую роль. Пользователи и роли настраиваются в панели управления.'),
    el('a', { class: 'btn secondary', href: '#/admin' }, 'Открыть управление пользователями')),
    el('div', { class: 'manager-grid' },
      card('Новая категория', actionForm('Создать категорию', [field('Код (латиница, цифры, _ или -)', categoryCode), field('Название', categoryName)],
        () => request('POST', '/api/competencies/categories', { code: categoryCode.value.trim(), name: categoryName.value.trim() }), refresh)),
      card('Новая компетенция', actionForm('Создать компетенцию', [field('Категория', category), field('Код (латиница, цифры, _ или -)', code), field('Название', name)],
        () => request('POST', '/api/competencies/catalog', { category_id: category.value, code: code.value.trim(), name: name.value.trim() }), refresh))));
  root.append(card('Новый отдел', actionForm('Создать отдел', [field('Код отдела (латиница, цифры, _ или -)', departmentCode), field('Название отдела', departmentName)],
    () => request('POST', '/api/competencies/departments', { code: departmentCode.value.trim(), name: departmentName.value.trim() }), refresh)));
  const appointments = el('div', { class: 'evaluation-grid' });
  const personName = user => user.full_name ? `${user.full_name}${user.login ? ' · ' + user.login : ''}` : user.login || 'Сотрудник';
  for (const department of departments.filter(d => d.is_active)) {
    const eligible = role => users.filter(u => u.status === 'active' && u.department_id === department.id && u.roles.includes(role));
    const manager = el('select', {}, el('option', { value: '' }, 'Без руководителя'),
      ...eligible('manager').map(u => el('option', { value: u.id }, personName(u))));
    manager.value = department.manager_id || '';
    const responsible = select(eligible('kk_responsible').map(u => ({ id: u.id, name: personName(u) })), 'Выберите ответственного');
    const appointed = (department.responsible_ids || []).map(id => users.find(u => u.id === id)).filter(Boolean);
    appointments.append(card(department.name,
      actionForm('Сохранить руководителя', [field('Руководитель', manager)],
        () => request('PUT', `/api/competencies/departments/${department.id}/manager`, { manager_id: manager.value || null }), refresh),
      actionForm('Назначить ответственного', [field('Ответственный за КК', responsible)],
        () => request('POST', `/api/competencies/departments/${department.id}/responsibles`, { user_id: responsible.value }), refresh),
      el('p', { class: 'cm-muted' }, `Назначены ответственные: ${appointed.length ? appointed.map(personName).join(', ') : 'нет'}`)));
  }
  root.append(appointments);
  root.append(el('h2', {}, 'Каталог компетенций'));
  if (!catalog.categories.length) root.append(empty('Добавьте первую категорию.'));
  const groups = el('div', { class: 'evaluation-grid' });
  for (const group of catalog.categories) {
    const competencies = catalog.competencies.filter(c => c.category_id === group.id);
    groups.append(card(group.name, competencies.length ? el('ul', { class: 'cm-list' }, ...competencies.map(c => el('li', {}, c.name))) : empty('Компетенций пока нет.')));
  }
  root.append(groups);
  return root;
}

export async function renderCompetencies() {
  const root = el('div', { class: 'competency-module' }, el('header', { class: 'cm-hero' },
    el('span', { class: 'eyebrow' }, 'Развитие команды'), el('h1', {}, 'Карта компетенций'),
    el('p', {}, 'Навыки, цели и тестирование — в одном рабочем пространстве.')));
  const roles = state.user.roles || [];
  const tabs = [{ key: 'employee', title: 'Мои компетенции', render: employeeArea }];
  if (roles.includes('manager')) tabs.push({ key: 'manager', title: 'Руководитель', render: refresh => departmentArea('manager', refresh) });
  if (roles.includes('kk_responsible')) tabs.push({ key: 'responsible', title: 'Ответственный за КК', render: refresh => departmentArea('responsible', refresh) });
  if (roles.includes('admin')) tabs.push({ key: 'admin', title: 'Каталог и назначения', render: adminArea });
  const tabbar = el('div', { class: 'cm-tabs', role: 'tablist', 'aria-label': 'Рабочие области' });
  const content = el('div', { role: 'tabpanel', id: 'competency-panel' });
  const discardNotice = notice();
  let generation = 0;
  let current = tabs.find(t => t.key === new URLSearchParams(location.hash.split('?')[1] || '').get('tab')) || tabs[0];
  let mounted = null;
  const confirmDiscard = action => {
    if (!mounted?.hasUnsavedChanges?.()) return false;
    discardNotice.className = 'cm-notice is-error';
    discardNotice.replaceChildren('Есть несохранённые изменения тестов. Сохраните их или подтвердите сброс перед переходом.',
      el('button', { type: 'button', class: 'btn secondary', onclick: () => {
        discardNotice.replaceChildren('Переход отменён. Изменения сохранены в редакторе.');
      } }, 'Остаться'),
      el('button', { type: 'button', class: 'btn secondary', onclick: async () => {
        discardNotice.replaceChildren();
        await action();
      } }, 'Сбросить правки и продолжить'));
    return true;
  };
  const load = async (message = '', discard = false) => {
    if (!discard && confirmDiscard(() => load(message, true))) return false;
    discardNotice.replaceChildren();
    const ticket = ++generation;
    content.replaceChildren(notice('Загрузка…'));
    try {
      const area = await current.render(load);
      if (ticket === generation) { mounted = area; content.replaceChildren(notice(message), area); }
    } catch (error) {
      if (ticket === generation) content.replaceChildren(notice(errorText(error), true),
        el('button', { class: 'btn secondary', onclick: () => load() }, 'Повторить загрузку'));
    }
  };
  const buttons = tabs.map(tab => el('button', { class: 'cm-tab', type: 'button', role: 'tab',
    id: `competency-tab-${tab.key}`, 'aria-controls': 'competency-panel', onclick: async () => {
       if (tab === current) return;
       const switchTab = async () => {
         current = tab;
         history.replaceState(null, '', `#/competencies?tab=${tab.key}`);
         updateTabs();
         await load('', true);
       };
       if (confirmDiscard(switchTab)) return;
       await switchTab();
    } }, tab.title));
  function updateTabs() {
    buttons.forEach((button, index) => button.setAttribute('aria-selected', String(tabs[index] === current)));
    content.setAttribute('aria-labelledby', `competency-tab-${current.key}`);
  }
  updateTabs();
  tabbar.append(...buttons);
  root.append(tabbar, discardNotice, content);
  await load();
  return root;
}
