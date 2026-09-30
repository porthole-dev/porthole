export function matches(text, query, repository, selected) {
  return (!selected || repository === selected) && query.toLowerCase().trim().split(/\s+/).every(word => text.toLowerCase().includes(word));
}

export function setupCatalog() {
  const mount = document.getElementById('catalog-tools');
  if (!mount || mount.dataset.ready) return;
  mount.dataset.ready = 'true';
  const content = mount.closest('.sl-markdown-content');
  let heading;
  const groups = [];
  for (const node of content.querySelectorAll('h2, table')) {
    if (node.tagName === 'H2') heading = node;
    else if (node.querySelector('th')?.textContent?.trim() === 'Package') {
      groups.push({ heading, table: node, repository: heading.textContent.trim(), rows: [...node.querySelectorAll('tbody tr')] });
    }
  }
  mount.innerHTML = '<label>Search packages <input type="search" placeholder="Name, description, version…" aria-controls="package-results"></label> <label>Repository <select><option value="">All repositories</option></select></label><p id="package-results" role="status" aria-live="polite"></p>';
  const input = mount.querySelector('input');
  const select = mount.querySelector('select');
  const status = mount.querySelector('[role="status"]');
  for (const group of groups) select.add(new Option(group.repository, group.repository));
  function update() {
    let count = 0;
    for (const group of groups) {
      let visible = 0;
      for (const row of group.rows) {
        row.hidden = !matches(row.textContent, input.value, group.repository, select.value);
        if (!row.hidden) visible++;
      }
      count += visible;
      group.table.hidden = !visible;
      // Hide only this repository's introductory metadata, keeping setup visible.
      for (let node = group.heading.closest('.sl-heading-wrapper') || group.heading; node; node = node.nextElementSibling) {
        node.hidden = !visible;
        if (node === group.table) {
          if (!group.rows.length && node.nextElementSibling?.tagName === 'P') node.nextElementSibling.hidden = !visible;
          break;
        }
      }
    }
    status.textContent = count ? `${count} package ${count === 1 ? 'entry' : 'entries'} shown` : 'No matching packages. Try another name or repository.';
  }
  input.addEventListener('input', update);
  select.addEventListener('change', update);
  update();
}
