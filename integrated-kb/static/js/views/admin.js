import { state, el } from '../app.js';
import { request as api, errorText, notice } from './testing.js';

const ROLES = { employee: 'Сотрудник', moderator: 'Модератор', admin: 'Администратор',
  manager: 'Руководитель', kk_responsible: 'Ответственный за КК' };

function button(label, action) {
  const status = notice();
  const control = el('button', { type: 'button', class: 'btn secondary', style: 'margin:4px;', onclick: async () => {
    if (control.disabled) return;
    control.disabled = true;
    try {
      const changed = await action();
      if (changed === false) return;
      const root = control.closest('.competency-module');
      const updated = await renderAdmin();
      updated.prepend(notice('Изменения сохранены.'));
      root.replaceWith(updated);
    } catch (e) { status.textContent = errorText(e); status.className = 'cm-notice is-error'; }
    finally { control.disabled = false; }
  } }, label);
  return el('span', { class: 'cm-inline-action' }, control, status);
}

function selectDepartment(departments, current) {
  const select = el('select', {}, el('option', { value: '' }, 'Без отдела'),
    ...departments.filter(d => d.is_active || d.id === current).map(d =>
      el('option', { value: d.id }, `${d.name} (${d.code})`)));
  select.value = current || '';
  return select;
}

function roleBoxes(selected) {
  const boxes = Object.entries(ROLES).map(([code, name]) => {
    const input = el('input', { type: 'checkbox', value: code });
    input.checked = selected.includes(code);
    return { input, element: el('label', { style: 'display:inline-block;margin:6px;' }, input, name) };
  });
  return { elements: boxes.map(b => b.element), values: () => boxes.filter(b => b.input.checked).map(b => b.input.value) };
}

export async function renderAdmin() {
  const [users, departments] = await Promise.all([
    api('GET', '/api/admin/users'), api('GET', '/api/admin/departments')
  ]);
  const root = el('div', { class: 'competency-module' }, el('h1', {}, 'Управление пользователями и подразделениями'),
    el('a', { href: '#/competencies?tab=admin' }, '← Каталог компетенций'));
  const usersArea = el('section', { class: 'cm-card' }, el('h2', {}, 'Пользователи'));
  const login = el('input', { placeholder: 'Логин' });
  const email = el('input', { placeholder: 'Email', type: 'email' });
  const name = el('input', { placeholder: 'ФИО' });
  const password = el('input', { placeholder: 'Пароль от 12 символов', type: 'password' });
  const department = selectDepartment(departments);
  const creationRoles = roleBoxes(['employee']);
  usersArea.append(el('div', {}, login, email, name, password,
    el('label', {}, 'Отдел: ', department), ...creationRoles.elements,
    button('Создать пользователя', () => api('POST', '/api/admin/users', {
      login: login.value.trim(), email: email.value.trim(), full_name: name.value.trim(),
      password: password.value, department_id: department.value || null, roles: creationRoles.values()
    }))));
  for (const user of users) {
    const target = selectDepartment(departments, user.department_id);
    const roles = roleBoxes(user.roles || []);
    const line = el('div', { style: 'border-top:1px solid #ddd;padding:12px 0;' },
      el('strong', {}, `${user.full_name} (${user.login}) — ${user.status}`),
      el('div', {}, 'Отдел: ', target,
        button('Сохранить отдел', () => api('PUT', `/api/admin/users/${user.id}/department`,
          { department_id: target.value || null }))),
      el('div', {}, ...roles.elements,
        button('Сохранить роли', () => api('PUT', `/api/admin/users/${user.id}/roles`,
          { roles: roles.values() }))));
    if (user.id !== state.user.id && user.status === 'active') line.append(
      el('details', {}, el('summary', {}, 'Заблокировать пользователя'),
        el('p', {}, `Подтвердите блокировку: ${user.full_name || user.login || 'Сотрудник'}. Доступ к системе будет закрыт.`),
        button('Подтвердить блокировку', () => api('DELETE', `/api/admin/users/${user.id}`, {}))));
    usersArea.append(line);
  }
  const deptArea = el('section', { class: 'cm-card' }, el('h2', {}, 'Подразделения и назначения'),
    button('Подготовить роли руководителя и ответственного за КК', () => api('POST', '/api/admin/competency-roles', {})));
  const code = el('input', { placeholder: 'Код отдела' });
  const deptName = el('input', { placeholder: 'Название отдела' });
  deptArea.append(el('div', {}, code, deptName,
    button('Создать отдел', () => api('POST', '/api/competencies/departments',
      { code: code.value.trim(), name: deptName.value.trim() }))));
  for (const dept of departments) {
    const managers = users.filter(u => u.status === 'active' && u.department_id === dept.id && u.roles.includes('manager'));
    const responsibles = users.filter(u => u.status === 'active' && u.department_id === dept.id && u.roles.includes('kk_responsible'));
    const manager = el('select', {}, el('option', { value: '' }, 'Без руководителя'),
      ...managers.map(u => el('option', { value: u.id }, u.full_name)));
    manager.value = dept.manager_id || '';
    const responsible = el('select', {}, el('option', { value: '' }, 'Выберите ответственного'),
      ...responsibles.map(u => el('option', { value: u.id }, u.full_name)));
    const box = el('div', { style: 'border-top:1px solid #ddd;padding:12px 0;' },
      el('strong', {}, `${dept.name} (${dept.code})`),
      el('div', {}, 'Руководитель: ', manager,
        button('Сохранить', () => api('PUT', `/api/competencies/departments/${dept.id}/manager`,
          { manager_id: manager.value || null }))),
      el('div', {}, 'Ответственный: ', responsible,
        button('Назначить', () => {
          if (!responsible.value) throw new Error('Выберите сотрудника');
          return api('POST', `/api/competencies/departments/${dept.id}/responsibles`, {user_id: responsible.value});
        })));
    for (const uid of dept.responsible_ids) {
      const u = users.find(person => person.id === uid);
      box.append(el('div', {}, u?.full_name || u?.login || 'Сотрудник недоступен',
        button('Снять назначение', () => api('DELETE',
          `/api/competencies/departments/${dept.id}/responsibles/${uid}`, {}))));
    }
    deptArea.append(box);
  }
  root.append(deptArea, usersArea);
  return root;
}
