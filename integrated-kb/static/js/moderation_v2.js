import { api } from './app.js';

async function request(method, url, body) {
  const result = await api(method, url, body);
  // The shared transport still returns legacy 404 values instead of throwing.
  if (result?.error) throw Object.assign(new Error(result.error), result);
  return result;
}

function errorMessage(error) {
  if (error.status === 409) return 'Заявка устарела: версия статьи или статус проверки изменились. Обновите очередь.';
  if (error.code === 'self_review') return 'Нельзя проверять собственную статью. Администратор может только одобрить её.';
  if (error.status === 403) return 'Нет доступа к модерации этой статьи или сессия истекла.';
  if (error.status === 401) return 'Войдите в систему повторно.';
  if (error.status === 404) return 'Заявка или очередь не найдена. Обновите страницу.';
  return 'Не удалось выполнить запрос. Попробуйте обновить очередь.';
}

export async function render() {
  const wrap = document.createElement('div');
  wrap.className = 'card';
  wrap.style.padding = '32px';
  wrap.append(Object.assign(document.createElement('h1'), { textContent: 'CRM: очередь модерации' }));

  let queue;
  try { queue = await request('GET', '/api/moderation/queue'); }
  catch (error) {
    wrap.append(Object.assign(document.createElement('p'), { textContent: errorMessage(error), role: 'alert' }));
    return wrap;
  }

  if (queue.length === 0) {
    wrap.append(Object.assign(document.createElement('p'), { textContent: 'Очередь пуста. Все статьи проверены!', style: 'color: #6b7280; margin-top: 16px;' }));
    return wrap;
  }

  const table = document.createElement('table');
  table.style.width = '100%';
  table.style.borderCollapse = 'collapse';
  
  table.innerHTML = `
    <thead><tr style="background:#f9fafb; border-bottom:1px solid #e5e7eb;">
      <th style="padding:12px 16px; text-align:left; color:#4b5563; font-size:13px; width: 30%;">Статья</th>
      <th style="padding:12px 16px; text-align:left; color:#4b5563; font-size:13px; width: 20%;">Автор</th>
      <th style="padding:12px 16px; text-align:left; color:#4b5563; font-size:13px; width: 15%;">Статус</th>
      <th style="padding:12px 16px; text-align:left; color:#4b5563; font-size:13px; width: 15%;">Модератор</th>
      <th style="padding:12px 16px; text-align:right; color:#4b5563; font-size:13px; width: 20%;">Действия</th>
    </tr></thead>
    <tbody></tbody>`;
  const tbody = table.querySelector('tbody');

  queue.forEach((row) => {
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid #f3f4f6';
    
    let statusText = row.status;
    if (row.status === 'in_progress') statusText = 'В работе';
    if (row.status === 'pending') statusText = 'Ожидает';

    tr.innerHTML = `
      <td style="padding:12px 16px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"><a href="#/page/${row.page_id}" style="font-weight: 500; color:#2563eb;">${escapeHtml(row.title)}</a></td>
      <td style="padding:12px 16px; font-size:14px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(row.author)}</td>
      <td style="padding:12px 16px;"><span style="font-size:12px; padding:2px 8px; border-radius:12px; white-space:nowrap; background:${row.status === 'in_progress' ? '#dbeafe; color:#1e40af' : '#fef3c7; color:#92400e'}">${statusText}</span></td>
      <td style="padding:12px 16px; font-size:14px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(row.moderator || '—')}</td>
      <td style="padding:12px 16px; text-align: right; white-space:nowrap;"></td>`;
    const td = tr.lastElementChild;

    if (row.status === 'pending') {
      const ok = document.createElement('button');
      ok.className = 'btn';
      ok.textContent = 'Одобрить';
      ok.style.marginRight = '8px';
      ok.disabled = row.can_approve !== true;
      if (ok.disabled && row.reason === 'self_review') ok.title = 'Нельзя одобрить собственную статью без роли администратора.';
      ok.addEventListener('click', async () => {
        if (ok.disabled) return;
        try { await request('POST', `/api/moderation/decide/${row.id}`, { action: 'approve' }); refresh(); }
        catch (e) { if (e.status === 409) { ok.disabled = true; no.disabled = true; } alert(errorMessage(e)); }
      });
      
      const no = document.createElement('button');
      no.className = 'btn danger';
      no.textContent = 'Отклонить';
      no.style.marginRight = '8px';
      no.disabled = row.can_reject !== true;
      if (no.disabled && row.reason === 'self_review') no.title = 'Нельзя отклонить собственную статью.';
      no.addEventListener('click', async () => {
        if (no.disabled) return;
        const comment = prompt('Причина отклонения (обязательно):');
        if (!comment) return;
        try { await request('POST', `/api/moderation/decide/${row.id}`, { action: 'reject', comment }); refresh(); }
        catch (e) { if (e.status === 409) { ok.disabled = true; no.disabled = true; } alert(errorMessage(e)); }
      });
      td.append(ok, no);
    }

    // Page deletion remains available to the author or admin on the page view.

    tbody.append(tr);
  });

  function refresh() { location.hash = '#/moderation'; location.reload(); }

  wrap.append(table);
  return wrap;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
