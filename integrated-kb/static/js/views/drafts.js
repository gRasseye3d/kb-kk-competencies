import { state, el, api, navigate } from '../app.js';

export async function renderDrafts() {
  const pages = await api('GET', '/api/tree');
  
  // Фильтруем только черновики текущего пользователя
  const myDrafts = pages.filter(p => p.status === 'draft' && p.created_by === state.user.id);

  const cards = el('div', { class: 'dashboard-grid' },
    ...myDrafts.map(page => {
      return el('div', { class: 'dash-card', style: 'cursor:pointer;' },
        el('div', { class: 'tag', style: 'color: #f59e0b;' }, 'Черновик'),
        el('h3', { class: 'title' }, page.title),
        el('p', { class: 'desc' }, 'Статья сохранена, но пока не опубликована.'),
        el('div', { style: 'display:flex; justify-content:space-between; align-items:center; margin-top:12px;' },
          el('button', { 
            class: 'btn secondary', 
            style: 'padding: 6px 12px; font-size: 13px;',
            onclick: (e) => { e.stopPropagation(); navigate(`#/edit/${page.id}`); }
          }, '✏ Редактировать'),
          el('button', { 
            class: 'btn', 
            style: 'padding: 6px 12px; font-size: 13px; background: #10b981;',
            onclick: async (e) => { 
              e.stopPropagation(); 
              try {
                await api('POST', `/api/moderation/submit/${page.id}`, {});
                alert('Отправлено на модерацию!');
                navigate('#/drafts');
              } catch (err) { alert('Ошибка: ' + err.message); }
            }
          }, '📤 На модерацию')
        )
      );
    })
  );

  return el('div', { style: 'max-width: 1200px; margin: 0 auto;' },
    el('h1', { style: 'margin-bottom: 24px;' }, '📝 Мои черновики'),
    myDrafts.length ? cards : el('p', { style: 'color: #6b7280;' }, 'У вас пока нет черновиков.')
  );
}
