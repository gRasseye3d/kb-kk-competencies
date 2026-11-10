function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function api(method, url, body, csrf) {
  const res = await fetch(url, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify({ ...body, csrf }) : undefined,
  });
  if (!res.ok) throw new Error((await res.json()).error || res.statusText);
  return res.json();
}

async function getMe() {
  const res = await fetch('/api/me');
  return (await res.json()).user;
}

export async function renderComments(pageId) {
  const me = await getMe();
  const container = document.createElement('div');
  container.className = 'comments';

  const load = async () => {
    const tree = await fetch(`/api/pages/${pageId}/comments`).then(r => r.json());
    container.innerHTML = '<h2>Комментарии</h2>';
    if (me) {
      const form = document.createElement('div');
      form.innerHTML = '<textarea placeholder="Оставьте комментарий…"></textarea>';
      const btn = document.createElement('button');
      btn.className = 'btn'; btn.textContent = 'Отправить';
      btn.addEventListener('click', async () => {
        const content = form.querySelector('textarea').value.trim();
        if (!content) return;
        await api('POST', `/api/pages/${pageId}/comments`, { content }, me.csrf);
        load();
      });
      form.append(btn);
      container.append(form);
    } else {
      container.append(Object.assign(document.createElement('p'),
        { textContent: 'Войдите, чтобы комментировать.' }));
    }
    container.append(renderTree(tree, 0));
  };

  const renderTree = (nodes, depth) => {
    const ul = document.createElement('div');
    for (const c of nodes) {
      const wrap = document.createElement('div');
      wrap.className = 'comment' + (c.deleted ? ' deleted' : '');
      wrap.style.marginLeft = depth * 20 + 'px';
      wrap.innerHTML = `
        <div class="meta">${escapeHtml(c.author || 'Аноним')} — ${new Date(c.created_at).toLocaleString()}</div>
        <div class="content">${escapeHtml(c.content)}</div>
      `;
      const actions = document.createElement('div');
      actions.className = 'actions';
      if (me && !c.deleted) {
        const replyBtn = document.createElement('button');
        replyBtn.className = 'btn secondary';
        replyBtn.textContent = 'Ответить';
        replyBtn.addEventListener('click', () => {
          const form = document.createElement('div');
          form.innerHTML = '<textarea></textarea>';
          const send = document.createElement('button');
          send.className = 'btn'; send.textContent = 'Отправить';
          send.addEventListener('click', async () => {
            const content = form.querySelector('textarea').value.trim();
            if (!content) return;
            await api('POST', `/api/pages/${pageId}/comments`,
                      { content, parent_id: c.id }, me.csrf);
            load();
          });
          form.append(send);
          wrap.append(form);
        });
        actions.append(replyBtn);
        if (c.author_id === me.id || me.roles?.includes('admin')) {
          const del = document.createElement('button');
          del.className = 'btn danger'; del.textContent = 'Удалить';
          del.addEventListener('click', async () => {
            if (!confirm('Удалить комментарий?')) return;
            await api('DELETE', `/api/comments/${c.id}`, {}, me.csrf);
            load();
          });
          actions.append(del);
        }
      }
      wrap.append(actions);
      if (c.children?.length) wrap.append(renderTree(c.children, depth + 1));
      ul.append(wrap);
    }
    return ul;
  };

  await load();
  return container;
}
