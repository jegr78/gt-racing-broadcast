// Shared audience rendering for live pages and the isolated builder preview.
(() => {
  'use strict';
  const deadlines = new WeakMap();
  const widgets = root => [...root.querySelectorAll('[data-audience-widget]')];
  function put(el, selector, value) {
    const node = el.querySelector(selector);
    if (node) node.textContent = value;
    return node;
  }
  function format(value, decimals, precision) {
    const digits = precision && precision.significantDigits;
    if (Number.isInteger(digits) && digits >= 1 && digits <= 15) {
      return new Intl.NumberFormat('en-US', {maximumSignificantDigits:digits}).format(value === 0 ? 0 : value);
    }
    const places = Number.isInteger(decimals) && decimals >= 0 && decimals <= 6 ? decimals : 0;
    const n = Math.abs(value) < .5 * 10 ** -places ? 0 : value;
    return new Intl.NumberFormat('en-US', {minimumFractionDigits: places, maximumFractionDigits: places}).format(n);
  }
  function expire(root, now = performance.now()) {
    widgets(root).forEach(el => {
      const deadline = deadlines.get(el);
      if (deadline !== undefined && now >= deadline) el.hidden = true;
    });
  }
  function render(root, data, now = performance.now(), transitMs = 0) {
    if (!data || data.ok === false || !Array.isArray(data.widgets)) return false;
    const transit = Number.isFinite(transitMs) && transitMs >= 0 ? transitMs / 1000 : 120;
    const rows = new Map(data.widgets.filter(row => row && typeof row.id === 'string').map(row => [row.id,row]));
    widgets(root).forEach(el => {
      const row = rows.get(el.id);
      if (!row || row.available !== true || !Number.isFinite(row.value)) {
        el.hidden = true;
        deadlines.delete(el);
        return;
      }
      put(el,'.audience-label',typeof row.label === 'string' ? row.label : '');
      put(el,'.audience-value',format(row.value,row.decimals,row.precision));
      put(el,'.audience-unit',typeof row.unit === 'string' ? row.unit : '');
      const hasTarget = Number.isFinite(row.target);
      const target = put(el,'.audience-target',hasTarget ? ' / ' + format(row.target,row.decimals) : '');
      if (target) target.hidden = !hasTarget;
      const percentage = put(el,'.audience-percent',hasTarget && Number.isFinite(row.percentage)
        ? format(row.percentage,0) + '%' : '');
      if (percentage) percentage.hidden = !hasTarget || !Number.isFinite(row.percentage);
      const track = el.querySelector('.audience-track');
      const fill = el.querySelector('.audience-fill');
      if (track) track.hidden = !hasTarget || !Number.isFinite(row.fill);
      if (fill) fill.style.width = Number.isFinite(row.fill) ? Math.max(0,Math.min(100,row.fill)) + '%' : '0%';
      const deadline = row.holdFor === null ? Infinity
        : Number.isFinite(row.holdFor) ? now + Math.max(0,Math.min(120,row.holdFor) - transit) * 1000 : now;
      deadlines.set(el,deadline);
      el.hidden = false;
    });
    expire(root,now);
    return true;
  }
  window.RacecastAudience = {render,expire};
  if (document.currentScript && document.currentScript.hasAttribute('data-audience-live')) {
    const boot = () => {
      if (!widgets(document).length) return;
      let pending = false;
      async function tick() {
        if (pending) return;
        pending = true;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(),4000);
        const started = performance.now();
        try {
          const response = await fetch('/audience/data',{cache:'no-store',signal:controller.signal});
          if (response.ok) {
            const data = await response.json();
            const received = performance.now();
            render(document,data,received,received - started);
          }
        } catch (_) { /* retain manual values; automatic deadlines still expire */ }
        finally { clearTimeout(timeout); pending = false; }
      }
      tick();
      setInterval(tick,1000);
      setInterval(() => expire(document),250);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',boot,{once:true});
    else boot();
  }
})();
