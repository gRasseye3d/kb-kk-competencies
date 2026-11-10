import { state, el, api, navigate, $ } from '../app.js';

export async function renderWelcome() {
  const firstName = state.user && state.user.full_name ? state.user.full_name.split(' ')[0] : 'Гость';

  const spaces = await api('GET', '/api/admin/spaces');
  const tree = await api('GET', '/api/tree');

  const recentPages = [...tree]
    .filter(page => {
      if (!page.content) return false;
      const textOnly = page.content.replace(/<[^>]+>/g, '').replace(/&nbsp;/g, ' ').trim();
      return textOnly.length > 0;
    })
    .sort((a, b) => b.id.localeCompare(a.id))
    .slice(0, 4);

  const hero = el('div', { class: 'hero-banner' },
    el('div', { class: 'hero-text' },
      el('h1', {}, `Добрый день, ${firstName}! 👋`),
      el('p', {}, 'Исследуйте, учитесь и делитесь знаниями с вашей командой. Наша база знаний помогает находить нужную информацию быстрее.'),
    ),
    el('button', { class: 'btn', onclick: () => navigate('#/new') }, '➕ Создать статью')
  );

  const cards = el('div', { class: 'dashboard-grid' },
    ...recentPages.map(page => {
      const space = spaces.find(s => s.id === page.space_id);
      return el('div', { class: 'dash-card', onclick: () => navigate(`#/page/${page.id}`), style: 'cursor:pointer;' },
        el('div', { class: 'tag' }, space ? space.name : 'База знаний'),
        el('h3', { class: 'title' }, page.title),
        el('p', { class: 'desc' }, 'Нажмите, чтобы прочитать статью целиком и посмотреть комментарии.'),
        el('div', { style: 'display:flex; justify-content:flex-end; align-items:center; margin-top:12px; font-size:13px; font-weight:500;' },
          el('span', { style: `padding: 4px 10px; border-radius: 12px; font-size: 12px; ${page.status === 'published' ? 'background:#dcfce7; color:#166534;' : 'background:#fef3c7; color:#92400e;'}` }, 
            page.status === 'published' ? 'Опубликовано' : 'Черновик'
          )
        )
      );
    })
  );

  return el('div', {},
    hero,
    el('h2', { style: 'margin-bottom:16px;' }, 'Недавние статьи'),
    recentPages.length ? cards : el('p', { style: 'color: #6b7280;' }, 'Пока нет статей с текстом. Создайте первую!')
  );
}
