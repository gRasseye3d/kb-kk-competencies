import { highlightAllIn } from './highlight.js';

export function openPreview(title, html) {
  const modal = document.createElement('div');
  modal.className = 'modal-backdrop';
  const box = document.createElement('div');
  box.className = 'modal';
  box.innerHTML = `<button class="close">×</button>
    <h1>${escapeHtml(title || 'Без заголовка')}</h1>
    <div class="article-body"></div>`;
  box.querySelector('.close').addEventListener('click', () => modal.remove());
  box.querySelector('.article-body').innerHTML = html;
  highlightAllIn(box);
  modal.append(box);
  document.getElementById('modalRoot').append(modal);
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
