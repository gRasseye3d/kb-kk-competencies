const KEYWORDS = {
  python: ['def','class','return','if','elif','else','for','while','import','from','as','try','except','finally','with','lambda','yield','raise','pass','break','continue','and','or','not','in','is','None','True','False','async','await','global','nonlocal','del','assert'],
  js: ['function','const','let','var','return','if','else','for','while','do','switch','case','break','continue','new','class','extends','super','this','typeof','instanceof','in','of','try','catch','finally','throw','async','await','yield','import','export','default','null','undefined','true','false'],
  sql: ['SELECT','FROM','WHERE','JOIN','LEFT','RIGHT','INNER','OUTER','ON','GROUP','BY','ORDER','HAVING','LIMIT','OFFSET','INSERT','INTO','VALUES','UPDATE','SET','DELETE','CREATE','TABLE','INDEX','PRIMARY','KEY','FOREIGN','REFERENCES','NOT','NULL','AND','OR','AS','DISTINCT','UNION','ALL','CASE','WHEN','THEN','ELSE','END','BEGIN','COMMIT','ROLLBACK'],
  html: [],
  css: [],
};

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function highlightCode(code, lang) {
  if (!lang) return esc(code);
  const kw = KEYWORDS[lang] || [];
  const pattern = new RegExp(
    [
      '(#[^\\n]*|//[^\\n]*|/\\*[\\s\\S]*?\\*/|--[^\\n]*)',
      '("(?:\\\\.|[^"\\\\])*"|\'(?:\\\\.|[^\'\\\\])*\'|`(?:\\\\.|[^`\\\\])*`)',
      '\\b(\\d+(?:\\.\\d+)?)\\b',
      kw.length ? '\\b(' + kw.join('|') + ')\\b' : '(\\b__nope__\\b)',
    ].join('|'),
    'g'
  );
  let out = '', last = 0, m;
  while ((m = pattern.exec(code))) {
    out += esc(code.slice(last, m.index));
    if (m[1]) out += `<span class="tok-com">${esc(m[0])}</span>`;
    else if (m[2]) out += `<span class="tok-str">${esc(m[0])}</span>`;
    else if (m[3]) out += `<span class="tok-num">${esc(m[0])}</span>`;
    else if (m[4]) out += `<span class="tok-kw">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  out += esc(code.slice(last));
  return out;
}

export function highlightAllIn(root) {
  root.querySelectorAll('pre code, pre').forEach(pre => {
    const lang = pre.getAttribute('data-lang') || 'js';
    const code = pre.textContent;
    pre.innerHTML = highlightCode(code, lang);
  });
}

export { highlightCode };
