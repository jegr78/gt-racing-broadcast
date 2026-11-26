/* The driver dashboard's track map, shared by the overlay (/drivers), the Race Control desk
   and the cockpit's timing area. TrackMap.create(svg) draws into an SVG with viewBox
   0 0 100 100 and returns {sync(track), draw(entries)}: sync loads the picked layout's line
   from /drivers/line on a track change and asks again while it is missing; draw puts one
   dot per entry with x and z on the line, labelled with its number or else its position. */
(function () {
  "use strict";
  const SVG = "http://www.w3.org/2000/svg";
  const LINE_RETRY_MS = 10000;   // the relay may download the line later
  const DOT_R = 2.8, CLEAR = 2 * DOT_R + 0.4;
  function safeColor(c, fallback) {
    return /^(#[0-9a-fA-F]{3,8}|rgba?\([0-9., ]+\)|[a-zA-Z]+)$/.test(c || "") ? c : fallback;
  }
  // Each dot sits on the line at its car's spot (spots in running order). One that would cover a
  // dot ahead slides back by up to one and a half dot widths, never past the next car's spot, else it overlaps.
  function placeDots(line, spots) {
    const n = line.length, cum = [0];
    for (let i = 1; i <= n; i++) {
      const a = line[i - 1], b = line[i % n];
      cum.push(cum[i - 1] + Math.hypot(b[0] - a[0], b[1] - a[1]));
    }
    const total = cum[n] || 1;
    function at(s) {
      s = ((s % total) + total) % total;
      let i = 1;
      while (i < n && cum[i] < s) i++;
      const a = line[i - 1], b = line[i % n], len = cum[i] - cum[i - 1];
      const f = len > 0 ? (s - cum[i - 1]) / len : 0;
      return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
    }
    function station(p) {
      let best = 0, bestD = Infinity;
      for (let i = 0; i < n; i++) {
        const a = line[i], b = line[(i + 1) % n], dx = b[0] - a[0], dy = b[1] - a[1];
        const len2 = dx * dx + dy * dy;
        const f = len2 > 0 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2)) : 0;
        const d = Math.hypot(a[0] + dx * f - p[0], a[1] + dy * f - p[1]);
        if (d < bestD) { bestD = d; best = cum[i] + f * Math.sqrt(len2); }
      }
      return best;
    }
    const st = spots.map(station), placed = [];
    function free(q) { return placed.every(function (o) { return Math.hypot(o[0] - q[0], o[1] - q[1]) >= CLEAR; }); }
    return st.map(function (s, i) {
      const gap = i + 1 < st.length ? ((s - st[i + 1]) % total + total) % total : Infinity;
      const room = Math.min(1.5 * CLEAR, gap), own = at(s);
      let spot = own;
      for (let k = 1; k <= 4 && !free(spot); k++) spot = at(s - room * k / 4);
      if (!free(spot)) spot = own;
      placed.push(spot);
      return spot;
    });
  }
  function create(svg) {
    let line = null, lineTrack = null, lineRetryAt = 0, view = null, cars = null;
    function project(x, z) {
      return [(x - view.x0) * view.k + view.dx, (z - view.z0) * view.k + view.dz];
    }
    function drawLine() {
      svg.replaceChildren();
      cars = null;
      if (!line || !line.points || line.points.length < 2) { view = null; return; }
      const xs = line.points.map(function (p) { return p[0]; }), zs = line.points.map(function (p) { return p[1]; });
      const x0 = Math.min(...xs), z0 = Math.min(...zs), w = Math.max(...xs) - x0 || 1, h = Math.max(...zs) - z0 || 1;
      const k = 92 / Math.max(w, h);
      view = {x0: x0, z0: z0, k: k, dx: 4 + (92 - w * k) / 2, dz: 4 + (92 - h * k) / 2};
      const path = document.createElementNS(SVG, "polyline");
      path.setAttribute("class", "line");
      path.setAttribute("vector-effect", "non-scaling-stroke");
      path.setAttribute("points", line.points.map(function (p) { return project(p[0], p[1]).join(","); }).join(" ") +
                        " " + project(line.points[0][0], line.points[0][1]).join(","));
      svg.appendChild(path);
      cars = document.createElementNS(SVG, "g");
      svg.appendChild(cars);
    }
    function draw(entries) {
      if (!cars || !view) return;
      const placedCars = entries.filter(function (e) { return typeof e.x === "number" && typeof e.z === "number"; });
      const spots = placeDots(line.points.map(function (q) { return project(q[0], q[1]); }),
                              placedCars.map(function (e) { return project(e.x, e.z); }));
      const dots = placedCars.map(function (e, i) { return [e, spots[i]]; });
      cars.replaceChildren(...dots.reverse().map(function (item) {   // the car ahead is drawn last, on top
        const e = item[0], p = item[1];
        const dot = document.createElementNS(SVG, "g");
        dot.setAttribute("class", "dot");
        const c = document.createElementNS(SVG, "circle");
        c.setAttribute("cx", p[0]); c.setAttribute("cy", p[1]); c.setAttribute("r", DOT_R);
        c.setAttribute("fill", safeColor(e.color, "#39414d"));
        c.setAttribute("stroke", "#fff"); c.setAttribute("stroke-width", 0.4);
        if (e.class_color) {
          c.setAttribute("stroke", safeColor(e.class_color, "#fff")); c.setAttribute("stroke-width", 0.8);
        }
        if (e.status === "est") {   // an estimated position: amber dashed ring, as the tower's EST tag
          c.setAttribute("stroke", "#ffb454"); c.setAttribute("stroke-width", 0.8);
          c.setAttribute("stroke-dasharray", "1 0.7");
        }
        const t = document.createElementNS(SVG, "text");
        t.setAttribute("x", p[0]); t.setAttribute("y", p[1]);
        t.setAttribute("fill", safeColor(e.text_color, "#fff"));
        t.textContent = e.number || String(e.pos);
        t.setAttribute("font-size", t.textContent.length > 2 ? 2.0 : 2.6);
        dot.appendChild(c); dot.appendChild(t);
        return dot;
      }));
    }
    async function load(track) {
      lineTrack = track;
      line = null;
      lineRetryAt = Date.now() + LINE_RETRY_MS;
      try {
        const res = await fetch("/drivers/line", {cache: "no-store"});
        if (res.ok) line = await res.json();
      } catch (e) { /* the map stays empty until the retry */ }
      drawLine();
    }
    function sync(track) {
      const due = track !== lineTrack || (track !== null && !line && Date.now() >= lineRetryAt);
      return due ? load(track) : Promise.resolve();
    }
    return {sync: sync, draw: draw};
  }
  window.TrackMap = {create: create};
})();
