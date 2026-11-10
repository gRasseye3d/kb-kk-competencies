import { state, $, el, navigate } from '../app.js';

export function renderTreeFromCache() {
  const container = $('#tree');
  if (!container) return;
  container.innerHTML = '';

  const rows = state.cache.tree || [];
  const spaces = state.cache.spaces || [];

  const pagesBySpace = {};
  for (const r of rows) {
    const sid = r.space_id ? String(r.space_id) : 'none';
    (pagesBySpace[sid] ||= []).push(r);
  }

  const iconFolder = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 8px; flex-shrink: 0;"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`;
  const iconFile = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6b7280" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 8px; flex-shrink: 0;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`;

  for (const space of spaces) {
    const spacePages = pagesBySpace[String(space.id)] || [];
    if (spacePages.length === 0) continue;

    const spaceWrap = el('div', { style: 'margin-bottom: 12px;' });
    
    const spaceTitle = el('div', { 
      style: 'display:flex; align-items:center; padding:6px 8px; font-size:13px; font-weight:600; color:#4b5563; cursor:pointer; border-radius:6px;'
    }, 
      el('span', { html: iconFolder, style: 'display:flex;' }),
      space.name
    );
    spaceWrap.append(spaceTitle);

    const byParent = {};
    for (const r of spacePages) {
      const pid = r.parent_id ? String(r.parent_id) : 'root';
      (byParent[pid] ||= []).push(r);
    }
    
    const renderNodes = (parentId, rootEl, depth) => {
      const children = byParent[parentId] || [];
      
      for (const node of children) {
        const nodeIdStr = String(node.id);
        const hasChildren = byParent[nodeIdStr] && byParent[nodeIdStr].length > 0;
        const isExpanded = state.expandedNodes.has(nodeIdStr);
        const isSelected = String(state.currentPage) === nodeIdStr;

        const row = el('div', {
          style: `display:flex; align-items:center; padding:6px 8px; padding-left:${depth * 16 + 8}px; cursor:pointer; border-radius:6px; font-size:13px; color:#4b5563; margin-bottom:2px;`
        });

        row.addEventListener('mouseenter', () => { if(!isSelected) row.style.background = '#f3f4f6'; });
        row.addEventListener('mouseleave', () => { if(!isSelected) row.style.background = 'transparent'; });
        if (isSelected) { row.style.background = '#eff6ff'; row.style.color = '#2563eb'; row.style.fontWeight = '500'; }

        if (hasChildren) {
          const arrow = el('span', { 
            style: `display:inline-block; width:16px; color:#9ca3af; font-size:16px; transition:transform 0.2s; transform: rotate(${isExpanded ? '90deg' : '0deg'}); margin-right:4px;`
          }, '›');
          arrow.addEventListener('click', (e) => {
            e.stopPropagation();
            if (isExpanded) state.expandedNodes.delete(nodeIdStr);
            else state.expandedNodes.add(nodeIdStr);
            renderTreeFromCache(); // Мгновенная перерисовка без запроса
          });
          row.append(arrow);
        } else {
          row.append(el('span', { style: 'width:20px;' }));
        }

        const iconHtml = hasChildren ? iconFolder : iconFile;
        row.append(el('span', { html: iconHtml, style: 'display:flex;' }));
        row.append(el('span', { style: 'flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;' }, node.title));

        row.addEventListener('click', () => {
          navigate(`#/page/${node.id}`);
          if (hasChildren && !isExpanded) {
            state.expandedNodes.add(nodeIdStr);
            renderTreeFromCache();
          }
        });

        rootEl.append(row);

        if (hasChildren && isExpanded) {
          const childWrap = el('div');
          renderNodes(nodeIdStr, childWrap, depth + 1);
          rootEl.append(childWrap);
        }
      }
    };
    
    renderNodes('root', spaceWrap, 0);
    container.append(spaceWrap);
  }
}
