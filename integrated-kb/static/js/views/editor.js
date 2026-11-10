import { state, $, el, api, navigate } from '../app.js';

export async function renderEditor(existing) {
  const { createEditor } = await import('../editor.js');
  const { renderLabelsField } = await import('../labels.js');
  
  const spaces = await api('GET', '/api/admin/spaces');
  const tree = await api('GET', '/api/tree');
  const availableSpaces = spaces.filter(s => existing ? s.id === existing.space_id : s.can_create === true);
  const canSave = existing ? existing.can_edit === true && availableSpaces.length > 0 : availableSpaces.length > 0;
  const accessMessage = existing
    ? 'Нет прав на редактирование этой статьи. Обратитесь к администратору пространства.'
    : 'Нет пространств, в которых вы можете создавать статьи. Для сохранения и отправки на модерацию попросите администратора выдать права редактора нужного пространства.';

  const labelsField = renderLabelsField(existing?.labels || []);
  const editor = createEditor(existing?.content || '', existing?.id || null);

  const main = $('#main');
  main.style.padding = '0';
  main.style.background = '#fff';

  // --- НАСТРОЙКИ ---
  const spacePathInput = el('select', { style: 'margin-bottom: 10px;' },
    ...availableSpaces.map(s => el('option', { value: s.id }, s.name)));
  spacePathInput.value = existing?.space_id || availableSpaces[0]?.id || '';

  // Synchronize available parents with the selected space.
  const parentSelect = el('select', {
    style: 'margin-bottom: 10px;'
  });

  const refreshParents = (preferredParent = null) => {
    const spaceId = spacePathInput.value;

    const availableParents = tree.filter(p =>
      p.space_id === spaceId &&
      (!existing || p.id !== existing.id)
    );

    parentSelect.replaceChildren(
      el('option', { value: '' }, '— Корень пространства —'),
      ...availableParents.map(p =>
        el('option', { value: p.id }, p.title)
      )
    );

    if (preferredParent &&
        availableParents.some(p => p.id === preferredParent)) {
      parentSelect.value = preferredParent;
    } else {
      parentSelect.value = '';
    }
  };

  refreshParents(existing?.parent_id || null);

  // Do not silently remove an existing parent that is not in the tree.
  if (existing?.parent_id &&
      !tree.some(p =>
        p.id === existing.parent_id &&
        p.space_id === existing.space_id
      )) {
    parentSelect.add(
      el('option', {
        value: existing.parent_id
      }, 'Текущий родитель (недоступен в списке)')
    );
    parentSelect.value = existing.parent_id;
  }

  if (existing) {
    spacePathInput.disabled = true;
  } else {
    spacePathInput.disabled = !canSave;
    spacePathInput.addEventListener('change', () => {
      refreshParents();
    });
  }

  // --- ЛОГИКА СОХРАНЕНИЯ ---
  const save = async (isModeration = false) => {
    if (!canSave) return alert(accessMessage);
    const spaceId = existing?.space_id || spacePathInput.value;
    if (!spaceId) return alert('Выберите пространство');
    if (!existing && !availableSpaces.some(s => s.id === spaceId)) return alert(accessMessage);

    const selectedParent = parentSelect.value || null;

    if (selectedParent &&
        selectedParent !== existing?.parent_id &&
        !tree.some(p =>
          p.id === selectedParent &&
          p.space_id === spaceId &&
          (!existing || p.id !== existing.id)
        )) {
      return alert(
        'Выбранная родительская статья не принадлежит этому пространству. ' +
        'Выберите родителя заново.'
      );
    }

    
    const payload = {
      title: titleInput.value,
      content: editor.getHTML(),
      space_id: spaceId,
      parent_id: selectedParent,
      labels: labelsField.getLabels(),
    };

    try {
      let pageId = existing?.id;
      
      // 1. Сохраняем статью
      if (existing) {
        await api('PUT', `/api/pages/${existing.id}`, payload);
      } else {
        const res = await api('POST', '/api/pages', payload);
        pageId = res.id;
      }

      // 2. Если нужно на модерацию — отправляем
      if (isModeration && pageId) {
        await api('POST', `/api/moderation/submit/${pageId}`, {});
        alert('Статья отправлена на модерацию!');
      }

      // Обновляем дерево и переходим на страницу
      state.cache.tree = null;
      state.cache.spaces = null;
      const { loadTree } = await import('../app.js');
      await loadTree();
      navigate(`#/page/${pageId}`);
      
    } catch (e) {
      const message = ['Space access denied', 'Page access denied'].includes(e.message)
        ? 'Нет прав на сохранение статьи в этом пространстве. Возможно, доступ изменился. Скопируйте введённый текст перед обновлением страницы и обратитесь к администратору пространства.'
        : e.message;
      alert('Ошибка сохранения: ' + message);
    }
  };

  // --- ЭЛЕМЕНТЫ ---
  const titleInput = el('input', {
    class: 'editor-title-input',
    value: existing?.title || '',
    placeholder: 'Название статьи...',
  });

  // Модальное окно настроек
  const openSettings = () => {
    const modal = el('div', { class: 'modal-backdrop' });
    const box = el('div', { class: 'modal', style: 'max-width: 500px;' },
      el('button', { class: 'close', onclick: () => modal.remove() }, '×'),
      el('h2', {}, 'Настройки статьи'),
      el('label', {}, 'Пространство:'), spacePathInput,
      el('label', {}, 'Вложить в папку:'), parentSelect,
      el('label', {}, 'Метки:'), labelsField.node
    );
    modal.append(box);
    document.getElementById('modalRoot').append(modal);
  };

  // --- ВЕРХНЯЯ ПАНЕЛЬ ---
  const topbar = el('div', { class: 'editor-topbar' },
    el('div', { style: 'display:flex; align-items:center; gap:16px;' },
      el('button', { class: 'btn secondary', onclick: () => history.back(), style: 'padding: 6px 12px;' }, '←'),
      el('span', { style: 'color:#6b7280; font-size:14px;' }, existing ? 'Редактирование' : 'Новая статья')
    ),
    el('div', { style: 'display:flex; align-items:center; gap:16px;' },
      el('span', { style: 'color:#10b981; font-size:13px; display:flex; align-items:center; gap:4px;' }, 
        el('span', { style: 'width:8px; height:8px; background:#10b981; border-radius:50%; display:inline-block;' }), 
        'Автосохранение'
      ),
      el('button', { class: 'btn secondary', onclick: openSettings }, '⚙️ Настройки'),
      // КНОПКА НА МОДЕРАЦИЮ
      el('button', { class: 'btn', style: 'background:#10b981;', disabled: canSave ? null : '', onclick: () => save(true) }, '📤 На модерацию'),
      el('button', { class: 'btn', disabled: canSave ? null : '', onclick: () => save(false) }, 'Сохранить')
    )
  );

  // --- ОСНОВНАЯ ЧАСТЬ ---
  const editorMain = el('div', { class: 'editor-main' },
    editor.toolbar,
    titleInput,
    editor.root
  );

  // --- ПРАВАЯ ПАНЕЛЬ ---
  const countWords = () => {
    const text = editor.root.innerText || '';
    return text.trim() ? text.trim().split(/\s+/).length : 0;
  };

  const wordCountDisplay = el('div', { style: 'font-size:32px; font-weight:700; color:var(--text-main);' }, countWords());
  const wordLabel = el('div', { style: 'color:var(--text-muted); font-size:13px;' }, 'слов');

  editor.root.addEventListener('input', () => {
    wordCountDisplay.textContent = countWords();
  });

  const sidebar = el('div', { class: 'editor-sidebar' },
    el('div', { class: 'card' },
      el('div', { class: 'sidebar-section-title' }, '📊 Статистика'),
      el('div', { style: 'display:flex; align-items:baseline; gap:8px;' }, wordCountDisplay, wordLabel)
    ),
    el('div', { class: 'card' },
      el('div', { class: 'sidebar-section-title' }, '⚡ Быстрые действия'),
      el('div', { class: 'quick-action-btn', onclick: () => editor.insertTable() }, '📊 Вставить таблицу'),
      el('div', { class: 'quick-action-btn', onclick: () => editor.insertCode() }, '💻 Блок кода'),
      el('div', { class: 'quick-action-btn', onclick: () => editor.insertVideo() }, '🎬 Загрузить видео')
    )
  );

  // --- СБОРКА ---
  return el('div', { style: 'display:flex; flex-direction:column; height:100%;' },
    topbar,
    !canSave && el('p', { role: 'status', style: 'padding: 12px 24px;' }, accessMessage),
    el('div', { class: 'editor-layout' },
      editorMain,
      sidebar
    )
  );
}
