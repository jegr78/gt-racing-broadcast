// League service widgets (#1255) on the live overlay pages and the builder canvas.
// Every string from the service is set as text; the markup is built from elements.
(() => {
  'use strict';
  const deadlines = new WeakMap();
  const slots = root => [...root.querySelectorAll('[data-league-widget]')];
  function node(tag, cls, text) {
    const el = document.createElement(tag);
    el.className = cls;
    if (text !== undefined) el.textContent = String(text);
    return el;
  }
  function fill(el, row) {
    const v = row.value || {};
    const parts = [];
    const title = node('div', 'lw-title', row.title || '');
    if (v.provisional === true) title.appendChild(node('span', 'lw-provisional', 'provisional'));
    parts.push(title);
    if (row.form === 'score') {
      const box = node('div', 'lw-values');
      (Array.isArray(v.values) ? v.values : []).forEach(item => {
        const it = node('div', 'lw-item');
        it.append(node('span', 'lw-label', item.label), node('span', 'lw-value', item.value));
        box.appendChild(it);
      });
      parts.push(box);
    } else if (row.form === 'table') {
      const grid = node('div', 'lw-table');
      if (typeof v.value_title === 'string' && v.value_title) grid.appendChild(node('span', 'lw-head', v.value_title));
      (Array.isArray(v.rows) ? v.rows : []).forEach(r => {
        grid.append(node('span', 'lw-rank', r.rank), node('span', 'lw-name', r.name), node('span', 'lw-row-value', r.value));
      });
      parts.push(grid);
    }
    el.replaceChildren(...parts);
  }
  function expire(root, now = performance.now()) {
    slots(root).forEach(el => {
      const deadline = deadlines.get(el);
      if (deadline !== undefined && now >= deadline) el.hidden = true;
    });
  }
  // data: {widgets: [{id, form, title, value, holdFor}]}; holdFor null never expires
  // (the builder's sample). A slot without a row hides.
  function render(root, data, now = performance.now(), transitMs = 0) {
    if (!data || data.ok === false || !Array.isArray(data.widgets)) return false;
    const transit = Number.isFinite(transitMs) && transitMs >= 0 ? transitMs / 1000 : 30;
    const rows = new Map(data.widgets.filter(r => r && typeof r.id === 'string').map(r => [r.id, r]));
    slots(root).forEach(el => {
      const row = rows.get(el.getAttribute('data-league-widget'));
      if (!row || row.form !== (el.classList.contains('lw-form-table') ? 'table' : 'score')) {
        el.hidden = true;
        deadlines.delete(el);
        return;
      }
      fill(el, row);
      deadlines.set(el, row.holdFor === null ? Infinity
        : Number.isFinite(row.holdFor) ? now + Math.max(0, Math.min(30, row.holdFor) - transit) * 1000 : now);
      el.hidden = false;
    });
    expire(root, now);
    return true;
  }
  window.RacecastLeague = {render, expire};
  if (document.currentScript && document.currentScript.hasAttribute('data-league-live')) {
    const boot = () => {
      if (!slots(document).length) return;
      let pending = false;
      async function tick() {
        if (pending) return;
        pending = true;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 4000);
        const started = performance.now();
        try {
          const response = await fetch('/league-widgets/data', {cache: 'no-store', signal: controller.signal});
          if (response.ok) {
            const data = await response.json();
            const received = performance.now();
            render(document, data, received, received - started);
          }
        } catch (_) { /* the relay is away: each value still hides at its deadline */ }
        finally { clearTimeout(timeout); pending = false; }
      }
      tick();
      setInterval(tick, 1000);
      setInterval(() => expire(document), 250);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
    else boot();
  }
})();
