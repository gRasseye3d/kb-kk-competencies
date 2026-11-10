import { state, el, api, navigate } from '../app.js';

export async function renderPage(id) {
  const page = await api('GET', `/api/pages/${id}`);
  
  if (!page || page.error) {
    return el('div', { class: 'card', style: 'text-align: center; padding: 60px 20px;' },
      el('div', { style: 'font-size: 48px; margin-bottom: 16px;' }, '🔍'),
      el('h1', { style: 'margin-bottom: 12px;' }, 'Статья не найдена'),
      el('button', { class: 'btn', onclick: () => navigate('#/') }, 'Вернуться на главную')
    );
  }

  const roles = state.user?.roles || [];
  const canEdit = state.user && (roles.includes('admin') ||
    (state.user.id === page.created_by && !['pending_review', 'archived'].includes(page.status)));
  const canDelete = canEdit;

  const body = el('div', { class: 'article-body' });
  body.innerHTML = page.content || '<p><em>Пусто</em></p>';
  const { highlightAllIn } = await import('../highlight.js');
  highlightAllIn(body);

  const labels = (page.labels || []).map(l => el('span', { class: 'label-chip' }, l));
  const actions = el('div', { style: 'display:flex;gap:8px;margin:12px 0;flex-wrap:wrap;' });
  
  const hasContent = page.content && page.content.replace(/<[^>]+>/g, '').replace(/&nbsp;/g, ' ').trim().length > 0;

  if (canEdit) {
    actions.append(el('button', { class: 'btn', onclick: () => navigate(`#/edit/${id}`) }, '✏ Редактировать'));
    
    if (['draft', 'rejected'].includes(page.status) && hasContent) {
      actions.append(el('button', { class: 'btn secondary', onclick: () => submitModeration(id) }, '📤 На модерацию'));
    }
  }

  // Legacy PDF and comments routes are unavailable with schema v4.
  
  if (roles.some(role => ['admin', 'moderator', 'manager', 'eol', 'EOL', 'god'].includes(role))) {
    actions.append(el('button', { class: 'btn secondary', onclick: () => showHistory(id, page.can_edit === true) }, '🕘 История'));
  }

  // КНОПКА УДАЛЕНИЯ
  if (canDelete) {
    actions.append(el('button', { 
      class: 'btn', 
      style: 'background: #fef2f2; color: #dc2626; border: 1px solid #fca5a5;',
      onclick: async () => {
        if (!confirm(`Скрыть статью "${page.title}"?`)) return;
        try {
          await api('DELETE', `/api/pages/${id}`, {});
          const { state } = await import('../app.js');
          state.cache.tree = null; // Сбрасываем кэш дерева
          navigate('#/');
        } catch (e) { alert('Ошибка удаления: ' + e.message); }
      }
    }, '🗑 Удалить'));
  }


  return el('div', { class: 'card' },
    el('h1', {}, page.title),
    el('div', { style: 'color:#6b7280;font-size:13px;margin-bottom:12px;' }, `Статус: ${statusLabel(page.status)}`),
    labels.length ? el('div', { class: 'labels-chosen' }, ...labels) : null,
    actions,
    body
  );
}

async function submitModeration(id) {
  try {
    await api('POST', `/api/moderation/submit/${id}`, {});
    alert('Отправлено на модерацию');
    const { state, render } = await import('../app.js');
    state.cache.tree = null; // Сброс кэша
    render();
  } catch (e) { alert(e.message); }
}

async function exportPdf(id) {
  const blob = await api('GET', `/api/pages/${id}/pdf`);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `article-${id}.pdf`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

async function showHistory(id, canEdit) {
  const rows = await api('GET', `/api/pages/${id}/history`);
  const modal = el('div', { class: 'modal-backdrop' });
  const box = el('div', { class: 'modal' },
    el('button', { class: 'close', onclick: () => modal.remove() }, '×'),
    el('h2', {}, 'История версий'),
    el('table', { class: 'queue' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Версия'), el('th', {}, 'Дата'), el('th', {}, 'Действие'))),
      el('tbody', {}, ...rows.map(r => el('tr', {},
        el('td', {}, r.version),
        el('td', {}, new Date(r.created_at).toLocaleString()),
        el('td', {}, canEdit ? el('button', {
          class: 'btn secondary',
          onclick: async () => {
            await api('POST', `/api/pages/${id}/restore/${r.id}`, {});
            modal.remove();
            const { state, render } = await import('../app.js');
            state.cache.tree = null;
            render();
          }
        }, 'Восстановить') : null)
      )))
    )
  );
  modal.append(box);
  document.getElementById('modalRoot').append(modal);
}

function statusLabel(s) {
  return ({
    draft: 'Черновик', pending: 'На модерации', in_progress: 'В работе',
    published: 'Опубликована', rejected: 'Отклонена',
  })[s] || s;
}
