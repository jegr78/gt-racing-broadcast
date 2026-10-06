() => {
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "BR", "OPTION", "IFRAME"]);
  const INTERACTIVE = "button, input, select, textarea, a[href], [role=button], [tabindex]:not([tabindex='-1'])";
  const NO_UA_CHECK = new Set(["checkbox", "radio", "range", "color", "file", "hidden", "image"]);
  const doc = document.documentElement;

  const rgba = s => {
    const m = /^rgba?\(([^)]+)\)$/.exec(s || "");
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };

  const bgChain = el => {
    const chain = [];
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      const c = rgba(cs.backgroundColor);
      if (c === null) return {chain, image: true};
      // The colour under a gradient still counts, so a body with gradients over a dark base reads as dark.
      if (c[3] > 0) chain.push(c);
      if (cs.backgroundImage !== "none") return {chain, image: true};
      if (c[3] >= 1) break;
    }
    return {chain, image: false};
  };

  const opacity = el => {
    let o = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity);
    return o;
  };

  const selector = el => {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && e !== doc && parts.length < 4; e = e.parentElement) {
      if (e.id) { parts.unshift("#" + CSS.escape(e.id)); break; }
      let s = e.tagName.toLowerCase();
      const cls = [...e.classList].slice(0, 2);
      if (cls.length) s += "." + cls.map(c => CSS.escape(c)).join(".");
      const sib = e.parentElement ? [...e.parentElement.children].filter(x => x.tagName === e.tagName) : [];
      if (sib.length > 1) s += `:nth-of-type(${sib.indexOf(e) + 1})`;
      parts.unshift(s);
    }
    return parts.join(" > ");
  };

  // An ancestor with non-visible overflow hides whatever lies outside its box, e.g. content scrolled out of a panel.
  const clippedAway = (el, r) => {
    let left = r.left, top = r.top, right = r.right, bottom = r.bottom;
    // Body and html overflow apply to the viewport, and a fixed box escapes the clipping of its ancestors.
    let fixed = getComputedStyle(el).position === "fixed";
    for (let p = el.parentElement; p && p !== document.body && !fixed; p = p.parentElement) {
      const ps = getComputedStyle(p);
      if (ps.overflowX !== "visible" || ps.overflowY !== "visible") {
        const pr = p.getBoundingClientRect();
        if (ps.overflowX !== "visible") { left = Math.max(left, pr.left); right = Math.min(right, pr.right); }
        if (ps.overflowY !== "visible") { top = Math.max(top, pr.top); bottom = Math.min(bottom, pr.bottom); }
        if (right - left <= 0 || bottom - top <= 0) return true;
      }
      fixed = ps.position === "fixed";
    }
    return false;
  };

  const ownText = el => [...el.childNodes].filter(n => n.nodeType === 3)
    .map(n => n.textContent).join("").trim().slice(0, 60);

  // UA defaults come from bare controls in an unstyled same-origin iframe.
  const frame = document.createElement("iframe");
  frame.style.cssText = "position:absolute;left:-9999px;top:0;width:10px;height:10px";
  document.body.appendChild(frame);
  const fd = frame.contentDocument;
  fd.open(); fd.write("<!doctype html><input><select></select><textarea></textarea><button>x</button>"); fd.close();
  // A page with color-scheme: dark gets dark UA controls, so the baseline must use the same scheme.
  fd.documentElement.style.colorScheme = getComputedStyle(doc).colorScheme;
  const ua = {};
  for (const tag of ["input", "select", "textarea", "button"]) {
    ua[tag] = frame.contentWindow.getComputedStyle(fd.querySelector(tag)).backgroundColor;
  }
  frame.remove();

  const scrollWidth = doc.scrollWidth;
  const index = new Map();
  const elements = [];
  for (const el of document.body.querySelectorAll("*")) {
    if (SKIP.has(el.tagName) || el.closest("svg")) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (!el.checkVisibility({opacityProperty: true, visibilityProperty: true})) continue;
    if (clippedAway(el, r)) continue;
    const x = r.left + scrollX, y = r.top + scrollY;
    if (x + r.width <= 0 || y + r.height <= 0 || x >= scrollWidth) continue;
    const cs = getComputedStyle(el);
    const tag = el.tagName.toLowerCase();
    const type = tag === "input" ? (el.type || "text") : "";
    let uaKey = null;
    if (tag === "input" && !NO_UA_CHECK.has(type)) uaKey = ["submit", "button", "reset"].includes(type) ? "button" : "input";
    else if (["select", "textarea", "button"].includes(tag)) uaKey = tag;
    const bg = bgChain(el);
    const rec = {
      i: elements.length, sel: selector(el), tag, interactive: el.matches(INTERACTIVE),
      text: ownText(el), has_text: (el.textContent || "").trim() !== "",
      rect: [x, y, r.width, r.height],
      client_w: el.clientWidth, scroll_w: el.scrollWidth,
      client_h: el.clientHeight, scroll_h: el.scrollHeight,
      overflow_x: cs.overflowX, overflow_y: cs.overflowY, text_overflow: cs.textOverflow,
      line_clamp: !!cs.webkitLineClamp && cs.webkitLineClamp !== "none",
      color: rgba(cs.color), bg_chain: bg.chain, bg_image: bg.image, opacity: opacity(el),
      font_size: parseFloat(cs.fontSize), font_weight: parseInt(cs.fontWeight, 10) || 400,
      disabled: !!el.disabled || el.getAttribute("aria-disabled") === "true",
      ua_key: uaKey, bg_raw: cs.backgroundColor,
      parent_bg: el.parentElement ? bgChain(el.parentElement).chain : [],
      anc: [],
    };
    if (rec.interactive) {
      for (let p = el.parentElement; p; p = p.parentElement) if (index.has(p)) rec.anc.push(index.get(p));
      index.set(el, rec.i);
    }
    elements.push(rec);
  }
  return {viewport: {w: doc.clientWidth, h: innerHeight}, scroll_width: scrollWidth, ua, elements};
}
