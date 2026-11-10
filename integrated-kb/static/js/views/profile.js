import { state, el, api, renderProfile } from '../app.js';

export async function renderProfilePage() {
  const roles = state.user.roles || [];
  const isAdmin = roles.includes('admin') || roles.includes('god') || state.user.login === 'admin';
  const isModerator = roles.includes('moderator') || roles.includes('eol') || roles.includes('manager') || isAdmin;
  
  const roleNames = {
    'god': 'Боги', 'eol': 'ЕОЛ', 'manager': 'Руководители', 'employee': 'Сотрудники',
    'admin': 'Администратор', 'moderator': 'Модератор', 'author': 'Автор', 'reader': 'Читатель',
    'kk_responsible': 'Ответственный за КК'
  };

  // --- АВАТАРКА ---
  const fileInput = el('input', { type: 'file', accept: 'image/*', style: 'display:none;' });
  
  const renderAvatarPreview = () => {
    if (state.avatarUrl) {
      return `<img src="${state.avatarUrl}" style="width:100px; height:100px; border-radius:50%; object-fit:cover; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">`;
    }
    return `<div style="width:100px; height:100px; border-radius:50%; background:#e5e7eb; display:flex; align-items:center; justify-content:center; font-size:36px; color:#9ca3af;">👤</div>`;
  };

  const avatarContainer = el('div', { 
    style: 'display:flex; flex-direction:column; align-items:center; margin-bottom:20px; cursor:pointer;',
    onclick: () => fileInput.click()
  },
    el('div', { html: renderAvatarPreview(), style: 'margin-bottom:12px;' }),
    el('div', { style: 'font-size:13px; color:var(--primary); font-weight:500;' }, 'Изменить фото')
  );

  fileInput.addEventListener('change', () => {
    const file = fileInput.files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) return alert('Файл слишком большой. До 2 МБ.');
    
    const reader = new FileReader();
    reader.onload = async () => {
      const base64Url = reader.result;
      avatarContainer.querySelector('div').innerHTML = `<img src="${base64Url}" style="width:100px; height:100px; border-radius:50%; object-fit:cover;">`;
      try {
        await api('PUT', '/api/admin/profile', { avatar_url: base64Url });
        state.avatarUrl = base64Url;
        renderProfile();
      } catch (e) { alert('Ошибка сохранения: ' + e.message); }
    };
    reader.readAsDataURL(file);
  });

  const settingsCard = el('div', { class: 'card' },
    el('h2', { style: 'text-align:center; margin-bottom:24px;' }, 'Настройки профиля'),
    fileInput,
    avatarContainer
  );

  const modCard = isModerator ? el('div', { class: 'card', style: 'border-left: 4px solid #10b981;' },
    el('h2', {}, '🛡 Модерация статей'),
    el('p', { style: 'color:#6b7280; margin-bottom:16px;' }, 'Очередь заявок на публикацию статей.'),
    el('button', { class: 'btn', style: 'background:#10b981;', onclick: () => location.hash = '#/moderation' }, 'Открыть очередь модерации')
  ) : null;

  const adminCard = isAdmin ? el('div', { class: 'card', style: 'border-left: 4px solid #3b82f6;' },
    el('h2', {}, '⚙️ Администрирование'),
    el('p', { style: 'color:#6b7280; margin-bottom:16px;' }, 'Управление пользователями, ролями и отделами.'),
    el('button', { class: 'btn', onclick: () => location.hash = '#/admin' }, 'Перейти в панель управления')
  ) : null;

  const competenceCard = el('div', { class: 'card' },
    el('h2', {}, '📋 Карта компетенций'),
    el('p', { style: 'color:#6b7280; margin-bottom:16px;' }, 'Матрица навыков и оценка сотрудников.'),
    el('button', { class: 'btn secondary', onclick: () => location.hash = '#/competencies' }, 'Открыть карту')
  );

  const roleCard = el('div', { class: 'card', style: 'background:#f8fafc; border:none;' },
    el('h3', { style: 'margin-bottom:12px; color: #374151;' }, 'Ваша роль в системе'),
    roles.length 
      ? el('div', { style: 'display:flex; gap:8px; flex-wrap:wrap;' }, 
          ...roles.map(r => el('span', { style: 'background:#e0e7ff; color:#3730a3; padding:6px 14px; border-radius:12px; font-size:14px; font-weight:600;' }, roleNames[r] || r))
        )
      : el('div', { style: 'display:flex; gap:8px; flex-wrap:wrap;' },
          el('span', { style: 'background:#e0e7ff; color:#3730a3; padding:6px 14px; border-radius:12px; font-size:14px; font-weight:600;' }, isAdmin ? 'Администратор (по логину)' : 'Не назначены')
        )
  );

  return el('div', { style: 'max-width: 800px; margin: 0 auto;' },
    el('h1', { style: 'margin-bottom:24px;' }, 'Личный кабинет'),
    el('div', { style: 'display:flex; flex-direction:column; gap:20px;' },
      roleCard,
      settingsCard,
      modCard,
      adminCard,
      competenceCard
    )
  );
}
