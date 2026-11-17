/* Driver telemetry controls shared by the Race Control desk and the Director Panel
   (#1114, #1115). DriverDesk.mount(el, {base, fullRedFlag, dashboardToggle}) polls the
   relay and renders: the invite link, the race with its finish and red flag controls,
   corrections, rejoin and change overrides, the dashboard's delay, fastest lap and
   qualifying corrections (plus its on-air switch with dashboardToggle), and the
   senders with readiness, reassign and disconnect. `base` prefixes every route: "/race-control" on the desk (its fetch
   shim adds /console), "" on the panel. Every text goes in through textContent. */
(function () {
  "use strict";
  var CSS = [
    ".dd{display:flex;flex-direction:column;gap:12px;padding:10px 12px;font-size:13px;color:var(--ink,#e8eaed)}",
    ".dd .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}",
    ".dd .grow{flex:1 1 auto;min-width:0}",
    ".dd .mono{font-family:var(--mono,ui-monospace,monospace);font-size:12px;overflow-wrap:anywhere}",
    ".dd .muted{color:var(--muted,#9aa0a6)}",
    ".dd button,.dd select,.dd input{background:var(--panel-2,#1b1f25);color:var(--ink,#e8eaed);border:1px solid var(--edge,#2a2f37);",
    "border-radius:6px;padding:5px 9px;font:inherit}",
    ".dd button{cursor:pointer}.dd button:hover{border-color:var(--ink,#e8eaed)}",
    ".dd button:disabled{opacity:.5;cursor:default}",
    ".dd button.warn{border-color:#ffb454;color:#ffb454}",
    ".dd button.alarm{border-color:#ff4d5e;color:#ff4d5e;font-weight:600}",
    ".dd button.armed{background:#ff4d5e;color:#fff}",
    ".dd input[type=number]{width:6.5em}",
    ".dd input[type=text]{width:9em}.dd input[type=text].wide{width:13em}",
    ".dd input[type=text].grow{width:auto;flex:1 1 12em}",
    ".dd input[type=checkbox]{width:auto;margin:0;padding:0}",
    ".dd label.check{display:inline-flex;align-items:center;gap:6px;cursor:pointer}",
    ".dd table{width:100%;border-collapse:collapse}",
    ".dd th,.dd td{text-align:left;padding:4px 6px;border-bottom:1px solid var(--edge,#2a2f37);vertical-align:middle}",
    ".dd th{color:var(--muted,#9aa0a6);font-weight:500;font-size:12px}",
    ".dd td.num{font-family:var(--mono,ui-monospace,monospace);text-align:right;white-space:nowrap}",
    ".dd .tbl{overflow-x:auto}",
    ".dd .pill{display:inline-block;border-radius:9px;padding:1px 7px;font-size:11px;border:1px solid var(--edge,#2a2f37)}",
    ".dd .pill.ok{border-color:#3ecf6e;color:#3ecf6e}",
    ".dd .pill.bad{border-color:#ff4d5e;color:#ff4d5e}",
    ".dd .pill.amb{border-color:#ffb454;color:#ffb454}",
    ".dd .banner{border:1px solid #ff4d5e;color:#ff4d5e;border-radius:6px;padding:6px 9px}",
    ".dd .msg{min-height:1.2em;font-size:12px}.dd .msg.err{color:#ff4d5e}",
    ".dd .stale{opacity:.45}",
    ".dd [hidden]{display:none!important}",
    ".dd select{width:auto;max-width:100%}",
    ".dd h3{margin:4px 0 0;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted,#9aa0a6)}"
  ].join("");

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "cls") n.className = attrs[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function opt(value, label, sel) {
    var o = el("option", {value: value, text: label});
    if (sel) o.selected = true;
    return o;
  }
  function secs(v) { return v == null ? "" : (v < 60 ? v.toFixed(1) : Math.floor(v / 60) + ":" + (v % 60).toFixed(1).padStart(4, "0")); }
  var STATUS = {racing: ["", ""], box: ["Box", "amb"], no_data: ["No data", "bad"],
                waiting: ["Waiting", ""], red_flag: ["Red flag", "bad"]};
  var ACTIONS = {correct: "correction", final_lap: "Final lap", finish: "Finish", reset: "reset",
                 red_flag: "RED FLAG", restart_mode: "restart mode", release: "restart released",
                 rejoin: "rejoin", change: "change override", settings: "settings",
                 reassign: "reassign", disconnect: "disconnect", red_flag_cover_down: "cover down",
                 fastest: "fastest lap", quali: "qualifying time", compound: "tyre compound"};
  var COMPOUNDS = [["", "—"], ["RS", "RS Racing Soft"], ["RM", "RM Racing Medium"], ["RH", "RH Racing Hard"],
                   ["I", "I Intermediate"], ["W", "W Wet"]];

  function lapTime(ms) {
    if (typeof ms !== "number" || ms <= 0) return "";
    var m = Math.floor(ms / 60000), s = (ms % 60000) / 1000;
    return m + ":" + s.toFixed(3).padStart(6, "0");
  }
  // "1:58.412" or "58.412" -> milliseconds; NaN when it is no lap time.
  function parseLap(text) {
    var t = String(text || "").trim().match(/^(?:(\d{1,2}):)?(\d{1,2}(?:\.\d{1,3})?)$/);
    if (!t) return NaN;
    return Math.round(((t[1] ? parseInt(t[1], 10) : 0) * 60 + parseFloat(t[2])) * 1000);
  }

  function mount(root, opts) {
    var base = (opts && opts.base) || "";
    var fullRedFlag = !!(opts && opts.fullRedFlag);
    var dashboardToggle = !!(opts && opts.dashboardToggle);
    if (!document.getElementById("dd-style")) {
      document.head.appendChild(el("style", {id: "dd-style", text: CSS}));
    }
    var state = {race: null, ready: null, invite: null, armed: null, armTimer: null};
    var msg = el("div", {cls: "msg", role: "status"});
    // Shown after STALE_POLLS failed polls: decisions must not rest on frozen gaps.
    var staleNote = el("div", {cls: "msg err", role: "alert"});
    staleNote.hidden = true;
    var STALE_POLLS = 3;
    function say(text, err) { msg.textContent = text || ""; msg.className = "msg" + (err ? " err" : ""); }

    async function call(path, body, quiet) {
      try {
        var r = await fetch(base + path, {method: body === undefined ? "GET" : "POST",
          headers: body === undefined ? {} : {"Content-Type": "application/json"},
          body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"});
        var d = await r.json().catch(function () { return {}; });
        if (!r.ok || d.ok === false) {
          if (!quiet) say(d.error || ("request failed (" + r.status + ")"), true);
          return null;
        }
        return d;
      } catch (e) { if (!quiet) say("relay not reachable", true); return null; }
    }
    async function act(path, body, done) {
      var d = await call(path, body || {});
      if (d) { say(done || ""); refresh(); }
      return d;
    }
    // Two clicks for anything that changes the race for everyone.
    function armed(btn, key, fn) {
      btn.addEventListener("click", function () {
        if (state.armed === key) {
          clearTimeout(state.armTimer); state.armed = null; btn.classList.remove("armed");
          btn.textContent = btn.dataset.label; fn(); return;
        }
        state.armed = key; btn.classList.add("armed"); btn.textContent = "Confirm " + btn.dataset.label;
        clearTimeout(state.armTimer);
        state.armTimer = setTimeout(function () {
          state.armed = null; btn.classList.remove("armed"); btn.textContent = btn.dataset.label;
        }, 4000);
      });
    }
    function button(label, cls, key, fn) {
      var b = el("button", {type: "button", cls: cls || "", text: label});
      b.dataset.label = label;
      if (key) armed(b, key, fn); else b.addEventListener("click", fn);
      return b;
    }

    // -- invite and last change ---------------------------------------------------
    var inviteText = el("span", {cls: "mono grow", text: "-"});
    var copyBtn = button("Copy", "", null, function () {
      if (!state.invite) return;
      (navigator.clipboard ? navigator.clipboard.writeText(state.invite) : Promise.reject())
        .then(function () { say("Invite link copied"); }, function () { say("Copy it by hand", true); });
    });
    var timingText = el("span", {cls: "mono grow", text: "-"});
    var timingCopy = button("Copy", "", null, function () {
      if (!state.timing) return;
      (navigator.clipboard ? navigator.clipboard.writeText(state.timing) : Promise.reject())
        .then(function () { say("Timing link copied"); }, function () { say("Copy it by hand", true); });
    });
    var lastChange = el("div", {cls: "muted"});

    // -- race controls ------------------------------------------------------------
    var mode = el("select", {"aria-label": "How the race finishes"},
                  [opt("laps", "Finish after laps"), opt("time", "Timed: Final lap by hand")]);
    var laps = el("input", {type: "number", min: "1", max: "999", placeholder: "laps", "aria-label": "Lap count"});
    mode.addEventListener("change", function () { act("/drivers/race/settings", {finish_mode: mode.value}, "Finish mode saved"); });
    laps.addEventListener("change", function () {
      var n = parseInt(laps.value, 10);
      act("/drivers/race/settings", {lap_count: isFinite(n) ? n : null}, "Lap count saved");
    });
    var finalLap = button("Final lap", "warn", "final", function () { act("/drivers/race/final-lap", {}, "Final lap"); });
    var finish = button("Finish", "warn", "finish", function () { act("/drivers/race/finish", {}, "Race finished"); });
    // The panel has its own RED FLAG for cover and banner; here it only holds the race.
    var redFlag = button(fullRedFlag ? "RED FLAG" : "Hold race (red flag)", "alarm", "red", async function () {
      if (!fullRedFlag) return act("/drivers/race/red-flag", {}, "Race held for a red flag");
      var d = await act("/red-flag", {on: true});
      if (!d) return;
      var miss = [];
      if (!d.cover) miss.push("cover: " + (d.note || "not raised"));
      if (d.banner !== "ok") miss.push("banner: " + d.banner);
      say(miss.length ? "RED FLAG held the race; " + miss.join("; ") : "RED FLAG: banner, cover and race held",
          miss.length > 0);
    });
    var restart = el("select", {"aria-label": "Restart after the red flag"},
                     [opt("keep", "Restart: keep gaps"), opt("neutralise", "Restart: neutralise")]);
    restart.addEventListener("change", function () {
      act("/drivers/race/restart-mode", {mode: restart.value}, "Restart mode set");
    });
    var release = button("Restart released", "", "release", function () {
      if (fullRedFlag) call("/red-flag", {on: false});
      act("/drivers/race/release", {}, "Restart released");
    });
    var redBanner = el("div", {cls: "banner", hidden: ""});

    // -- race table -----------------------------------------------------------------
    var raceBody = el("tbody");
    var raceTable = el("div", {cls: "tbl"}, [el("table", {}, [el("thead", {}, [el("tr", {}, [
      "Pos", "Entry", "Laps", "Gap", "Int.", "Status", "Pits", "Corr."].map(function (h) {
        return el("th", {text: h}); }))]), raceBody])]);
    var raceNote = el("div", {cls: "muted"});

    // -- correction / rejoin / change form ------------------------------------------
    var corrEntry = el("select", {"aria-label": "Entry to correct"});
    var corrM = el("input", {type: "number", step: "1", placeholder: "± metres", "aria-label": "Distance correction in metres"});
    var corrPit = el("input", {type: "number", step: "1", placeholder: "± pits", "aria-label": "Pit stop correction"});
    var corrWhy = el("input", {type: "text", maxlength: "200", placeholder: "reason", "aria-label": "Reason", cls: "grow"});
    var corrBtn = button("Apply correction", "", null, async function () {
      var m = parseFloat(corrM.value || "0"), p = parseInt(corrPit.value || "0", 10);
      var d = await act("/drivers/race/correct", {entry: corrEntry.value, metres: isFinite(m) ? m : 0,
                        pit_stops: isFinite(p) ? p : 0, reason: corrWhy.value}, "Correction applied");
      if (d) { corrM.value = ""; corrPit.value = ""; corrWhy.value = ""; }
    });
    var multiRow = el("div", {cls: "row", hidden: ""});
    var rejoinEntry = el("select", {"aria-label": "Team to rejoin"});
    var rejoinOn = button("Mark rejoin", "", null, function () {
      act("/drivers/race/rejoin", {entry: rejoinEntry.value, on: true}, "Rejoin marked");
    });
    var rejoinOff = button("Clear rejoin", "", null, function () {
      act("/drivers/race/rejoin", {entry: rejoinEntry.value, on: false}, "Rejoin cleared");
    });
    var changeSel = el("select", {"aria-label": "Sender to make active"});
    var changeBtn = button("Change now", "warn", "change", function () {
      var v = changeSel.value.split("\u0000");
      act("/drivers/race/change", {entry: v[0], conn: v[1]}, "Change applied");
    });
    multiRow.append(rejoinEntry, rejoinOn, rejoinOff, changeSel, changeBtn);

    // -- dashboard: on air, delay, fastest lap, qualifying ---------------------------
    var onAir = button("Show dashboard", "", null, function () {
      var show = onAir.dataset.on !== "1";
      act("/drivers/race/settings", {visible: show}, show ? "Dashboard on air" : "Dashboard off air");
    });
    onAir.hidden = !dashboardToggle;
    var delay = el("input", {type: "number", min: "0", max: "120", step: "1", placeholder: "delay s",
                             "aria-label": "Dashboard delay in seconds"});
    delay.addEventListener("change", function () {
      var n = parseInt(delay.value || "0", 10);
      act("/drivers/race/settings", {delay_s: isFinite(n) ? n : 0}, "Delay saved");
    });
    var flText = el("span", {cls: "grow"});
    var flClear = button("Clear fastest lap", "warn", "fl-clear", function () {
      act("/drivers/race/fastest", {}, "Fastest lap cleared");
    });
    var flEntry = el("select", {"aria-label": "Fastest lap entry"});
    var flDriver = el("input", {type: "text", maxlength: "32", placeholder: "driver", cls: "wide",
                                "aria-label": "Fastest lap driver"});
    var flTime = el("input", {type: "text", placeholder: "1:58.412", "aria-label": "Fastest lap time", size: "9"});
    var flSet = button("Set fastest lap", "", null, async function () {
      var ms = parseLap(flTime.value);
      if (!isFinite(ms) || ms <= 0) return say("Lap time as m:ss.mmm", true);
      var d = await act("/drivers/race/fastest", {entry: flEntry.value, driver: flDriver.value.trim(), ms: ms},
                        "Fastest lap set");
      if (d) { flDriver.value = ""; flTime.value = ""; }
    });
    var qEntry = el("select", {"aria-label": "Qualifying entry"});
    var qTime = el("input", {type: "text", placeholder: "1:58.412", "aria-label": "Qualifying time", size: "9"});
    var qSet = button("Set time", "", null, function () {
      var ms = parseLap(qTime.value);
      if (!isFinite(ms) || ms <= 0) return say("Lap time as m:ss.mmm", true);
      act("/drivers/race/quali", {entry: qEntry.value, ms: ms}, "Qualifying time set");
    });
    var qDelete = button("Delete time", "warn", "q-del", function () {
      act("/drivers/race/quali", {entry: qEntry.value, ms: null}, "Qualifying time deleted");
    });
    var qRestore = button("GT7 time", "", null, function () {
      act("/drivers/race/quali", {entry: qEntry.value, clear: true}, "GT7's time restored");
    });
    var qList = el("div", {cls: "muted"});

    // -- tyres: compound per entry and the two display switches ----------------------
    function toggle(label, key) {
      var box = el("input", {type: "checkbox", "aria-label": label});
      box.addEventListener("change", function () {
        var body = {}; body[key] = box.checked;
        act("/drivers/race/settings", body, label + (box.checked ? " on" : " off"));
      });
      var text = el("span", {cls: "muted", text: label});
      return {box: box, label: el("label", {cls: "check"}, [box, text])};
    }
    var showTyre = toggle("Show compound", "show_compound");
    var showFuel = toggle("Show fuel", "show_fuel");
    var tyreBody = el("tbody");
    var tyreTable = el("div", {cls: "tbl"}, [el("table", {}, [el("thead", {}, [el("tr", {}, [
      "Entry", "Compound", "Set by"].map(function (h) { return el("th", {text: h}); }))]), tyreBody])]);
    var tyreRows = Object.create(null);   // keyed by entry name: no inherited keys
    function tyreRow(entry) {
      var sel = el("select", {"aria-label": "Tyre compound of " + entry},
                   COMPOUNDS.map(function (c) { return opt(c[0], c[1]); }));
      sel.addEventListener("change", function () {
        act("/drivers/race/compound", {entry: entry, code: sel.value || null}, "Compound set");
      });
      var by = el("td", {cls: "muted"});
      var tr = el("tr", {}, [el("td", {text: entry}), el("td", {}, [sel]), by]);
      return tyreRows[entry] = {tr: tr, sel: sel, by: by};
    }
    function renderTyres(entries, compounds) {
      var rows = entries.map(function (e) {
        var t = tyreRows[e] || tyreRow(e);
        var c = Object.prototype.hasOwnProperty.call(compounds, e) ? compounds[e] : null;
        if (document.activeElement !== t.sel) t.sel.value = c ? c.code : "";
        t.by.textContent = c ? c.who + " at " + new Date(c.ts * 1000).toLocaleTimeString() : "";
        return t.tr;
      });
      if (!rows.length) rows = [el("tr", {}, [el("td", {colspan: "3", cls: "muted", text: "No entries yet."})])];
      tyreBody.replaceChildren.apply(tyreBody, rows);
    }

    // -- readiness and senders -------------------------------------------------------
    var readyHead = el("tr");
    var readyBody = el("tbody");
    var readyTable = el("div", {cls: "tbl"}, [el("table", {}, [el("thead", {}, [readyHead]), readyBody])]);
    var sendBody = el("tbody");
    var sendTable = el("div", {cls: "tbl"}, [el("table", {}, [el("thead", {}, [el("tr", {}, [
      "Sender", "Entry", "", ""].map(function (h) { return el("th", {text: h}); }))]), sendBody])]);

    root.replaceChildren(el("div", {cls: "dd"}, [
      staleNote,
      el("div", {cls: "row"}, [el("strong", {text: "Invite"}), inviteText, copyBtn]),
      el("div", {cls: "row"}, [el("strong", {text: "Timing"}), timingText, timingCopy]),
      lastChange,
      el("div", {cls: "row"}, [mode, laps, finalLap, finish]),
      el("div", {cls: "row"}, [redFlag, restart, release]),
      redBanner,
      el("h3", {text: "Race"}), raceNote, raceTable,
      el("div", {cls: "row"}, [corrEntry, corrM, corrPit, corrWhy, corrBtn]),
      multiRow,
      el("h3", {text: "Dashboard"}),
      el("div", {cls: "row"}, [onAir, el("label", {cls: "muted", text: "Delay (s)"}), delay]),
      el("div", {cls: "row"}, [flText, flClear]),
      el("div", {cls: "row"}, [flEntry, flDriver, flTime, flSet]),
      el("div", {cls: "row"}, [el("span", {cls: "muted", text: "Qualifying"}), qEntry, qTime, qSet, qDelete, qRestore]),
      qList,
      el("h3", {text: "Tyres"}),
      el("div", {cls: "row"}, [showTyre.label, showFuel.label]),
      tyreTable,
      el("h3", {text: "Readiness"}), readyTable,
      el("h3", {text: "Senders"}), sendTable,
      msg]));

    function keepSelect(sel, items) {
      var cur = sel.value;
      if (document.activeElement === sel) return;
      sel.replaceChildren.apply(sel, items.map(function (i) { return opt(i[0], i[1], i[0] === cur); }));
    }

    function renderRace(r) {
      var s = r.settings || {};
      onAir.dataset.on = s.visible ? "1" : "";
      onAir.textContent = s.visible ? "Hide dashboard" : "Show dashboard";
      onAir.className = s.visible ? "warn" : "";
      if (document.activeElement !== delay) delay.value = s.delay_s || 0;
      showTyre.box.checked = s.show_compound !== false;
      showFuel.box.checked = s.show_fuel !== false;
      var known = (r.entries || []).map(function (e) { return e.entry; });
      Object.keys(r.compounds || {}).forEach(function (e) { if (known.indexOf(e) < 0) known.push(e); });
      renderTyres(known, r.compounds || {});
      var fl = r.fastest;
      flText.textContent = fl ? "Fastest lap " + lapTime(fl.ms) + " · " + fl.driver + " (" + fl.entry + ")"
                              : "No fastest lap yet.";
      flClear.disabled = !fl;
      var q = r.quali_overrides || {};
      qList.textContent = Object.keys(q).length ? "Corrected: " + Object.keys(q).map(function (e) {
        return e + " " + (q[e] === null ? "deleted" : lapTime(q[e]));
      }).join(", ") : "";
      if (document.activeElement !== mode) mode.value = s.finish_mode || "laps";
      if (document.activeElement !== laps) laps.value = s.lap_count || "";
      laps.hidden = mode.value !== "laps";
      var red = r.red_flag;
      if (document.activeElement !== restart) restart.value = r.restart_mode || "keep";
      restart.hidden = !red && !(r.entries || []).some(function (e) { return e.status === "red_flag"; });
      release.hidden = !red;
      redBanner.hidden = !red;
      redBanner.textContent = red ? "Red flag: each car continues from its anchor when it leaves the box of the restart lobby (" +
        (red.mode === "neutralise" ? "neutralised" : "gaps kept") + ")." : "";
      var la = r.last_action;
      lastChange.textContent = la ? "Last change: " + (ACTIONS[la.action] || la.action) + " by " + la.who +
        " at " + new Date(la.ts * 1000).toLocaleTimeString() : "";
      raceNote.textContent = {no_line: "No distance: pick a track layout with a downloaded racing line.",
        waiting: "Waiting for the start.", final_lap: "Final lap.", finishing: "Finishing.",
        finished: "Finished."}[r.state] || "";
      var rows = (r.entries || []).map(function (e, i) {
        var st = STATUS[e.status] || [e.status, ""];
        var gap = e.lapped ? "+" + e.lapped + " L" : (i ? secs(e.gap_s) : "");
        var cells = [el("td", {cls: "num", text: String(i + 1)}),
          el("td", {}, [el("div", {text: e.entry}), el("div", {cls: "muted", text: e.driver})]),
          el("td", {cls: "num", text: String(e.laps)}),
          el("td", {cls: "num", text: gap}), el("td", {cls: "num", text: e.lapped ? "" : secs(e.interval_s)}),
          el("td", {}, [st[0] ? el("span", {cls: "pill " + st[1], text: st[0]}) : null,
                        e.finished ? el("span", {cls: "pill ok", text: "Finished"}) : null,
                        e.rejoin ? el("span", {cls: "pill amb", text: "Rejoin"}) : null,
                        e.position_mismatch ? el("span", {cls: "pill amb", text: "GT7 P" + e.gt7_position,
                                                           title: "GT7's position disagrees with the distance order"}) : null]),
          el("td", {cls: "num", text: String(e.pit_stops)}),
          el("td", {cls: "num", text: e.correction_m ? (e.correction_m > 0 ? "+" : "") + e.correction_m + " m" : ""})];
        return el("tr", {}, cells);
      });
      if (!rows.length) rows = [el("tr", {}, [el("td", {colspan: "8", cls: "muted", text: "No driver data yet."})])];
      raceBody.replaceChildren.apply(raceBody, rows);
    }

    function renderSenders(rd) {
      var multi = !!rd.multi_lobby;
      var entries = Array.from(new Set(rd.rows.map(function (r) { return r.entry; })));
      keepSelect(corrEntry, entries.map(function (e) { return [e, e]; }));
      multiRow.hidden = !multi;
      keepSelect(rejoinEntry, entries.map(function (e) { return [e, e]; }));
      keepSelect(flEntry, entries.map(function (e) { return [e, e]; }));
      keepSelect(qEntry, entries.map(function (e) { return [e, e]; }));
      var conns = [];
      rd.rows.forEach(function (r) {
        r.senders.forEach(function (s) { conns.push([r.entry + "\u0000" + s.conn, r.entry + ": " + s.name]); });
      });
      keepSelect(changeSel, conns);
      changeBtn.disabled = !conns.length;
      // Readiness: one row per entry; endurance shows the stint on air and the next one.
      var focus = multi ? (rd.focus || []).filter(Boolean) : [null];
      if (multi && !focus.length) focus = Array.from(new Set(rd.rows.map(function (r) { return r.segment; })));
      readyHead.replaceChildren.apply(readyHead, [el("th", {text: "Entry"})].concat(focus.map(function (f) {
        return el("th", {text: f || "Sender"}); })));
      var byKey = Object.create(null);
      rd.rows.forEach(function (r) { byKey[r.entry + "\u0000" + (multi ? r.segment : null)] = r; });
      var ready = entries.map(function (e) {
        return el("tr", {}, [el("td", {text: e})].concat(focus.map(function (f) {
          var r = byKey[e + "\u0000" + f];
          var live = r ? r.senders.filter(function (x) { return x.live; }) : [];
          return el("td", {}, [el("span", {cls: "pill " + (live.length ? "ok" : (r && r.senders.length ? "amb" : "bad")),
            text: live.length ? live.map(function (x) { return x.name; }).join(", ")
                  : (r && r.senders.length ? "quiet" : "no sender")})]);
        })));
      });
      if (!ready.length) ready = [el("tr", {}, [el("td", {cls: "muted", text: "No entries known yet."})])];
      readyBody.replaceChildren.apply(readyBody, ready);

      if (sendBody.contains(document.activeElement)) return;   // keep an open choice
      var rows = [];
      rd.rows.forEach(function (r) {
        r.senders.forEach(function (s) {
          var to = el("select", {"aria-label": "Move " + s.name + " to another entry"},
                      entries.filter(function (e) { return e !== r.entry; }).map(function (e) { return opt(e, e); }));
          rows.push(el("tr", {}, [
            el("td", {}, [el("div", {text: s.name}), el("span", {cls: "pill " + (s.live ? "ok" : "bad"),
                                                                 text: s.live ? "live" : "quiet"})]),
            el("td", {}, [el("div", {text: r.entry}), el("div", {cls: "muted", text: r.segment || ""})]),
            el("td", {}, [el("div", {cls: "row"}, [to, button("Move", "", null, function () {
              act("/drivers/reassign", {conn: s.conn, entry: to.value}, "Sender moved");
            })])]),
            el("td", {}, [button("Disconnect", "warn", "disc-" + s.conn, function () {
              act("/drivers/disconnect", {conn: s.conn}, "Sender disconnected");
            })])]));
        });
      });
      if (!rows.length) rows = [el("tr", {}, [el("td", {colspan: "4", cls: "muted", text: "No sender connected."})])];
      sendBody.replaceChildren.apply(sendBody, rows);
    }

    function markStale(ok) {
      if (ok) { state.fails = 0; state.staleSince = null; }
      else { state.fails = (state.fails || 0) + 1; state.staleSince = state.staleSince || new Date(); }
      var stale = state.fails >= STALE_POLLS;
      staleNote.hidden = !stale;
      if (stale) staleNote.textContent = "Relay not reachable since " + state.staleSince.toLocaleTimeString() +
        ": the tables below are not current.";
      [raceTable, readyTable, sendTable].forEach(function (t) { t.classList.toggle("stale", stale); });
    }
    async function refresh() {
      var r = await call("/drivers/race", undefined, true);
      markStale(!!r);
      if (r) renderRace(r);
      var rd = await call("/drivers/readiness", undefined, true);
      if (rd) renderSenders(rd);
    }
    async function refreshInvite() {
      var d = await call("/drivers/invite", undefined, true);
      if (!d) return;
      state.invite = d.active ? location.origin + "/console/drive?i=" + encodeURIComponent(d.id) : null;
      inviteText.textContent = state.invite || "No invite: it starts with the event.";
      copyBtn.disabled = !state.invite;
      var t = await call("/drivers/timing-link", undefined, true);
      if (!t) return;
      state.timing = t.active ? location.origin + "/console/timing?l=" + encodeURIComponent(t.id) : null;
      timingText.textContent = state.timing || "No timing link: it starts with the event.";
      timingCopy.disabled = !state.timing;
    }
    refresh(); refreshInvite();
    setInterval(refresh, 2000);
    setInterval(refreshInvite, 15000);
    return {refresh: refresh};
  }
  window.DriverDesk = {mount: mount};
})();
