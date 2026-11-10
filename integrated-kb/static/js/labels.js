export function renderLabelsField(initial) {
  const chosen = new Set(initial || []);
  const node = document.createElement('div');
  node.className = 'labels-field';

  const chips = document.createElement('div');
  chips.className = 'labels-chosen';

  const input = document.createElement('input');
  input.className = 'labels-input';
  input.placeholder = 'Метки: начните вводить…';

  const dropdown = document.createElement('div');
  dropdown.className = 'labels-dropdown';
  dropdown.hidden = true;

  const renderChips = () => {
    chips.innerHTML = '';
    for (const l of chosen) {
      const chip = document.createElement('span');
      chip.className = 'label-chip';
      chip.textContent = l;
      chip.addEventListener('click', () => { chosen.delete(l); renderChips(); });
      chips.append(chip);
    }
  };
  renderChips();

  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { dropdown.hidden = true; return; }
    timer = setTimeout(async () => {
      const res = await fetch(`/api/labels?q=${encodeURIComponent(q)}`);
      const items = await res.json();
      dropdown.innerHTML = '';
      const list = new Set([...items, q]);
      for (const name of list) {
        const d = document.createElement('div');
        d.textContent = name;
        d.addEventListener('click', () => {
          chosen.add(name.toLowerCase());
          input.value = '';
          dropdown.hidden = true;
          renderChips();
        });
        dropdown.append(d);
      }
      dropdown.hidden = false;
    }, 150);
  });

  node.append(chips, input, dropdown);

  return {
    node,
    getLabels: () => [...chosen],
  };
}
