// Shared text auto-fit (#957) for the relay overlay pages (HUD, Split HUD,
// Splitscreen labels, Intermission) and the Control Center builder canvas.
// A builder slot whose CSS sets --fit-max (and optionally --fit-min) shrinks its text
// from max down to min until it fits the slot's inner width; a slot without
// --fit-max keeps the font size its CSS sets. No per-element list: every [data-edit]
// slot is checked after each data change.
(() => {
  'use strict';
  const CAP = 500;              // overlay_build.FIT_SIZE_MAX: the loop steps 1px from max
  const fitted = new WeakSet(); // slots carrying an inline size from a fit

  // A custom property inherits by default, so a --fit-max on one slot would make every
  // descendant fit as well. Registered non-inheriting, it stays on the element that
  // sets it. The registry is per document and also covers the builder's shadow canvas.
  function register() {
    if (!(window.CSS && CSS.registerProperty)) return;
    for (const name of ['--fit-max', '--fit-min']) {
      try {
        CSS.registerProperty({name, syntax: '<length> | <number>', inherits: false, initialValue: '0'});
      } catch (_) { /* already registered */ }
    }
  }

  // Largest size in [min, max] at which the text fits the inner width. Measures the
  // text itself: a centred or right-aligned flex item overflows outside scrollWidth.
  // `scale` undoes a CSS-scaled ancestor (the zoomed builder canvas).
  function shrink(el, cs, max, min, scale) {
    const text = document.createRange();
    text.selectNodeContents(el);
    const width = el.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
    let size = Math.min(max, CAP);
    el.style.fontSize = size + 'px';
    fitted.add(el);
    while (size > min && text.getBoundingClientRect().width / scale > width) {
      size -= 1;
      el.style.fontSize = size + 'px';
    }
  }

  // Fit one slot. Returns whether it carries a fit. A hidden or zero-width slot keeps
  // its last size: measured there, any text would collapse to min.
  function fit(el, opts) {
    if (!el) return false;
    const cs = getComputedStyle(el);
    const max = parseFloat(cs.getPropertyValue('--fit-max'));
    if (!(max > 0)) {
      if (fitted.has(el)) { el.style.fontSize = ''; fitted.delete(el); }
      return false;
    }
    if (!el.getClientRects().length || !(el.clientWidth > 0)) return true;
    const lo = parseFloat(cs.getPropertyValue('--fit-min'));
    const min = lo > 0 ? lo : Math.min(max, 12);
    const scale = (opts && opts.scale) || 1;
    shrink(el, cs, max, min, scale);
    return true;
  }

  function fitAll(root, opts) {
    (root || document).querySelectorAll('[data-edit]').forEach(el => fit(el, opts));
  }

  // Live pages: refit after every DOM change the page's data render makes (text,
  // added rows, shown/hidden slots) and when a webfont arrives. Our own font-size
  // writes are style mutations too; takeRecords drops them, so the observer settles.
  function watch() {
    const observer = new MutationObserver(() => { fitAll(document); observer.takeRecords(); });
    observer.observe(document.body, {childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ['hidden', 'class', 'style']});
    const refit = () => { fitAll(document); observer.takeRecords(); };
    if (document.fonts) {
      document.fonts.ready.then(refit);
      document.fonts.addEventListener('loadingdone', refit);
    }
    refit();
    return observer;
  }

  register();
  window.RacecastFit = {register, fit, fitAll, watch};
  if (document.currentScript && document.currentScript.hasAttribute('data-fit-live')) {
    // Loaded at the end of the slot markup, so the body is already there to observe.
    if (!document.body) document.addEventListener('DOMContentLoaded', watch, {once: true});
    else watch();
  }
})();
