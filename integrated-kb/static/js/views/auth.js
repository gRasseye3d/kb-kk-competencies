import { state, $, el, api, loadMe, render } from '../app.js';

export async function renderAuth() {
  const loginWrap = el('div', { 
    style: 'display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%;' 
  },
    el('div', { 
      class: 'card', 
      style: 'width:100%; max-width:400px; padding:40px 30px;' 
    },
      el('div', { style: 'text-align:center; font-size:32px; margin-bottom:10px;' }, '📚'),
      el('h2', { style: 'text-align:center; margin-top:0; margin-bottom:20px;' }, 'Вход в базу знаний'),
      
      el('label', { style: 'display:block; font-size:13px; color:#666; margin-bottom:4px;' }, 'Логин'),
      el('input', { id: 'login', placeholder: 'admin', style: 'width:100%;padding:10px;margin-bottom:16px;border:1px solid #d1d5db;border-radius:6px;' }),
      
      el('label', { style: 'display:block; font-size:13px; color:#666; margin-bottom:4px;' }, 'Пароль'),
      el('input', { id: 'password', type: 'password', placeholder: '••••••••', style: 'width:100%;padding:10px;margin-bottom:24px;border:1px solid #d1d5db;border-radius:6px;' }),
      
      el('button', {
        class: 'btn',
        style: 'width:100%; padding:10px; font-size:16px;',
        onclick: async (e) => {
          const btn = e.target;
          btn.textContent = 'Вход...';
          btn.disabled = true;
          try {
            const data = await api('POST', '/api/login', {
              login: $('#login').value,
              password: $('#password').value,
            });
            state.user = { ...data.user, roles: [] };
            state.csrf = data.csrf;
            await loadMe();
            
            state.cache.tree = null;
            state.cache.spaces = null;
            const { loadTree } = await import('../app.js');
            await loadTree();
            
            render();
          } catch (err) {
            btn.textContent = 'Войти';
            btn.disabled = false;
            let errDiv = document.getElementById('login-error');
            if (!errDiv) {
              errDiv = el('div', { id: 'login-error', style: 'color:#dc2626; font-size:14px; margin-bottom:16px; text-align:center;' });
              btn.before(errDiv);
            }
            errDiv.textContent = err.message;
          }
        }
      }, 'Войти')
    )
  );

  loginWrap.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') document.querySelector('.btn').click();
  });

  return loginWrap;
}
