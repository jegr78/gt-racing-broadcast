/* Driver telemetry controls shared by the Race Control desk and the Director Panel
   (#1114, #1115). DriverDesk.mount(el, {base, fullRedFlag, dashboardToggle}) polls the
   relay and renders: the invite link, the race with its finish and red flag controls,
   the race recording with its disconnect events, corrections, estimates for quiet
   cars, retirements (DNF), shown driver names, rejoin and change overrides, the dashboard's delay, fastest lap and
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
    ".dd .nowrap{white-space:nowrap}",
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
    ".dd td.num.lap{white-space:normal}",
    ".dd .race th,.dd .race td{padding:4px}.dd .race .pills{display:flex;flex-wrap:wrap;gap:3px}",
    ".dd .pill{display:inline-block;white-space:nowrap;border-radius:9px;padding:1px 7px;font-size:11px;border:1px solid var(--edge,#2a2f37)}",
    ".dd .pill.ok{border-color:#3ecf6e;color:#3ecf6e}",
    ".dd .pill.bad{border-color:#ff4d5e;color:#ff4d5e}",
    ".dd .pill.amb{border-color:#ffb454;color:#ffb454}",
    ".dd .banner{border:1px solid #ff4d5e;color:#ff4d5e;border-radius:6px;padding:6px 9px}",
    ".dd .msg{min-height:1.2em;font-size:12px}.dd .msg.err{color:#ff4d5e}",
    ".dd .stale{opacity:.45}",
    ".dd [hidden]{display:none!important}",
    ".dd select{width:auto;max-width:100%}",
    ".dd a.btn{background:var(--panel-2,#1b1f25);color:var(--ink,#e8eaed);border:1px solid var(--edge,#2a2f37);",
    "border-radius:6px;padding:5px 9px;text-decoration:none}.dd a.btn:hover{border-color:var(--ink,#e8eaed)}",
    ".dd .rec{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--muted,#9aa0a6)}",
    ".dd .rec.on{background:#ff4d5e}",
    ".dd td.logo{width:30px;padding-right:0}.dd td.logo img{display:block;width:28px;height:20px;box-sizing:border-box;",
    "padding:1px;border-radius:3px;background:#f2f4f7;object-fit:contain}",
    ".dd tr.cls td{background:var(--panel-2,#1b1f25);font-weight:600}",
    ".dd tr.cls .lap{margin-left:8px;color:var(--muted,#9aa0a6);font-weight:400;font-family:var(--mono,ui-monospace,monospace)}",
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
  function secs(v) { return typeof v !== "number" ? "" : (v < 60 ? v.toFixed(1) : Math.floor(v / 60) + ":" + (v % 60).toFixed(1).padStart(4, "0")); }
  // The whole name if it fits; else the first name as an initial; else cut with an ellipsis.
  function fitName(name, max) {
    var s = String(name || "").trim();
    if (s.length <= max) return s;
    var parts = s.split(/\s+/);
    if (parts.length > 1) {
      s = parts[0].charAt(0) + ". " + parts.slice(1).join(" ");
      if (s.length <= max) return s;
    }
    return s.slice(0, max - 1) + "\u2026";
  }
  var NAME_MAX = 10;       // the race table's driver line
  var NAME_LIST_MAX = 16;  // events, readiness, senders and the fastest lap
  var STATUS = {racing: ["", ""], box: ["Box", "amb"], no_data: ["No data", "bad"], dc: ["DC", "bad"],
                est: ["EST", "amb"], waiting: ["Waiting", ""], red_flag: ["Red flag", "bad"],
                no_sender: ["No sender", ""], retired: ["DNF", "bad"]};
  var ACTIONS = {correct: "correction", final_lap: "Final lap", finish: "Finish", reset: "reset",
                 red_flag: "RED FLAG", restart_mode: "restart mode", release: "restart released",
                 rejoin: "rejoin", change: "change override", settings: "settings",
                 reassign: "reassign", disconnect: "disconnect", red_flag_cover_down: "cover down",
                 fastest: "fastest lap", quali: "qualifying time", compound: "tyre compound",
                 recording_start: "recording start", recording_stop: "recording stop",
                 dc_confirm: "DC confirmed", dc_dismiss: "DC dismissed",
                 estimate: "estimate", estimate_end: "estimate ended", retire: "retirement",
                 driver_name: "driver name", class_laps: "class lap count"};
  var EVENT_STATES = {suspected: ["Suspected DC", "bad"], dropout: ["Dropout", "amb"],
                      confirmed: ["Confirmed DC", "bad"], dismissed: ["Dismissed", ""]};
  var OFFSETS = [5, 10, 15, 20, 30];
  var SOUND_KEY = "racecast.desk.dcSound";
  function loadSound() { try { return localStorage.getItem(SOUND_KEY) === "1"; } catch (e) { return false; } }
  function saveSound(on) { try { localStorage.setItem(SOUND_KEY, on ? "1" : "0"); } catch (e) { /* private mode */ } }
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

  // The named classes whose lap count the desk sets: the first runs on the race's count, the unclassed on none.
  function lapClasses(names) { return names.filter(Boolean).slice(1); }
  // The named classes whose fastest lap the desk can set or clear; none outside class mode.
  function flClasses(r) { return (Array.isArray(r.classes) ? r.classes : []).filter(Boolean); }
  // The fastest lap of a desk scope: the race's, or with a class only that class's.
  function scopedFastest(r, cls) {
    if (!cls) return r.fastest || null;
    var f = r.class_fastest || {};
    return Object.prototype.hasOwnProperty.call(f, cls) && f[cls] ? f[cls] : null;
  }
  // The entries the fastest-lap form offers: a class only its own.
  function flEntries(r, cls) {
    return (r.entries || []).filter(function (e) { return !cls || e.class === cls; })
      .map(function (e) { return e.entry; });
  }
  function fastestBody(cls, body) { return cls ? Object.assign({class: cls}, body) : body; }
  // "+N L" and the exact time when there is one; no-break spaces let a narrow desk wrap
  // only between the lap count and the time.
  function lappedText(laps, time) {
    return "+" + laps + "\u00a0L" + (typeof time === "number" ? "\u00a0\u00b7 " + secs(time) : "");
  }
  // A race row's exact interval, also for a car a lap behind the car ahead.
  function intervalTime(e, grouped) { return grouped ? e.class_interval_time_s : e.interval_time_s; }
  // A race row's Gap cell; grouped rows read laps down within the class.
  function gapCell(e, grouped, i, k) {
    if (grouped) {
      return {text: e.class_lapped ? lappedText(e.class_lapped, e.class_gap_time_s) : (k ? secs(e.class_gap_s) : ""),
              lap: !!e.class_lapped};
    }
    return {text: e.lapped ? lappedText(e.lapped, e.gap_time_s) : (i ? secs(e.gap_s) : ""),
            lap: !!e.lapped};
  }
  // The race rows under their class: [{name, color, lap, laps, rows}] in class order; one group named null outside class mode.
  function classGroups(r) {
    var rows = r.entries || [], names = Array.isArray(r.classes) ? r.classes : [];
    if (!names.length) return [{name: null, rows: rows}];
    var lap = r.class_lap || {}, set = r.class_laps || {}, colors = r.class_colors || {}, first = names.filter(Boolean)[0];
    var counted = r.class_counts || {};
    var count = r.lap && typeof r.lap.count === "number" ? r.lap.count : null;
    function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k) && typeof o[k] === "number" ? o[k] : null; }
    return names.map(function (n) {
      var c = Object.prototype.hasOwnProperty.call(colors, n) ? colors[n] : "";
      return {name: n, lap: own(lap, n), laps: n === first ? count : counted[n] === true ? own(set, n) : null,
              color: typeof c === "string" && /^(#[0-9a-fA-F]{3,8}|(rgb|hsl)a?\([0-9a-zA-Z.,%\s\/]+\)|[a-zA-Z]{3,20})$/.test(c) ? c : "",
              rows: rows.filter(function (e) { return (e.class == null ? "" : e.class) === n; })};
    });
  }

  var logoFailed = {};   // brand key -> when its logo last failed to load
  // The entry's brand logo; a missing one leaves the cell empty for a minute.
  function logoCell(e) {
    var td = el("td", {cls: "logo"});
    var key = /^[a-z0-9-]{1,64}$/.test(e.brand || "") ? e.brand : "";
    if (!key || Date.now() - (logoFailed[key] || -Infinity) < 60000) return td;
    var img = el("img", {alt: e.brand_name || "", title: e.brand_name || ""});
    img.onerror = function () { logoFailed[key] = Date.now(); img.remove(); };
    img.src = (window.RC_API ? window.RC_API("/drivers/brand/" + key) : "/drivers/brand/" + key);
    td.appendChild(img);
    return td;
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
    var PIT_HINT = "Estimated: a stop counts when the car stands off the racing line, so a beached car " +
      "or a red flag stop beside the track counts too. Correct it with the pit correction.";
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
    // Lap counts of the slower classes for the stint on air; they never finish a car.
    var classLapRow = el("div", {cls: "row", hidden: ""});
    var classLapIn = Object.create(null);
    var classLapKey = null;
    function classLapInput(name) {
      var input = el("input", {type: "number", min: "1", max: "999", placeholder: "laps", "aria-label": "Lap count of " + name});
      input.addEventListener("change", function () {
        var n = parseInt(input.value, 10);
        act("/drivers/race/class-laps", {class: name, laps: isFinite(n) ? n : null}, name + " lap count saved");
      });
      return classLapIn[name] = input;
    }
    function renderClassLaps(r) {
      var names = lapClasses(Array.isArray(r.classes) ? r.classes : []);
      classLapRow.hidden = !names.length;
      var key = JSON.stringify(names);
      if (key !== classLapKey && !classLapRow.contains(document.activeElement)) {
        classLapKey = key;
        classLapRow.replaceChildren.apply(classLapRow, [el("span", {cls: "muted", text: "Class laps"})].concat(
          names.map(function (n) {
            return el("label", {cls: "check"}, [el("span", {text: n}), classLapIn[n] || classLapInput(n)]);
          })));
      }
      var set = r.class_laps || {};
      names.forEach(function (n) {
        var input = classLapIn[n];
        if (input && document.activeElement !== input) {
          input.value = Object.prototype.hasOwnProperty.call(set, n) && typeof set[n] === "number" ? set[n] : "";
        }
      });
    }

    // -- race table -----------------------------------------------------------------
    var raceBody = el("tbody");
    var raceTable = el("div", {cls: "tbl"}, [el("table", {cls: "race"}, [el("thead", {}, [el("tr", {}, [
      "Pos", "", "Entry", "Laps", "Gap", "Int.", "Status", "Pits", "Corr."].map(function (h) {
        return el("th", h === "Pits" ? {text: h, title: PIT_HINT} : {text: h}); }))]), raceBody])]);
    var raceNote = el("div", {cls: "muted"});

    // -- race recording and its disconnect events -------------------------------------
    // Links bypass the fetch shim, so they take the console prefix from RC_API themselves.
    function href(path) { var u = base + path; return window.RC_API ? window.RC_API(u) : u; }
    var recDot = el("span", {cls: "rec", "aria-hidden": "true"});
    var recText = el("span", {text: "Not recording"});
    var recStart = button("Start recording", "", null, function () {
      act("/drivers/race/recording", {action: "start"}, "Recording started");
    });
    var recStop = button("Stop recording", "warn", "rec-stop", function () {
      act("/drivers/race/recording", {action: "stop"}, "Recording stopped");
    });
    var recCsv = el("a", {cls: "btn", text: "Download CSV", href: href("/drivers/race/recording/csv"),
                          download: "race-recording.csv"});
    // Without a time the page opens at the end of the recording, with the slider over all of it.
    var recBrowse = el("a", {cls: "btn", text: "Browse recording", href: href("/drivers/snapshot"),
                             target: "_blank", rel: "noopener"});
    var recWarn = el("div", {cls: "msg err", role: "alert", hidden: ""});
    var evTitle = el("h3", {text: "Events (0)"});
    var evBody = el("tbody");
    var evTable = el("div", {cls: "tbl"}, [el("table", {}, [el("thead", {}, [el("tr", {}, [
      "Entry", "State", ""].map(function (h) { return el("th", {text: h}); }))]),
      evBody])]);
    var soundBox = el("input", {type: "checkbox", "aria-label": "Sound on suspicion"});
    soundBox.checked = loadSound();
    var audio = null;
    // Browsers allow audio only after a gesture, so the context starts on this click.
    function wakeAudio() {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      try {
        audio = audio || new AC();
        if (audio.state === "suspended") audio.resume().catch(function () {});
      } catch (e) { audio = null; }
    }
    soundBox.addEventListener("change", function () {
      saveSound(soundBox.checked);
      if (soundBox.checked) wakeAudio();
    });
    // A stored "on" survives a reload, but the audio waits for the first click on the desk.
    root.addEventListener("click", function () { if (soundBox.checked && !audio) wakeAudio(); });
    function beep() {
      if (!soundBox.checked || !audio) return;
      try {
        var o = audio.createOscillator(), g = audio.createGain(), t0 = audio.currentTime;
        o.frequency.value = 880;
        g.gain.setValueAtTime(0.2, t0);
        g.gain.exponentialRampToValueAtTime(0.001, t0 + 0.25);
        o.connect(g); g.connect(audio.destination);
        o.start(t0); o.stop(t0 + 0.25);
      } catch (e) { /* no audio device */ }
    }
    var evEmpty = el("tr", {}, [el("td", {colspan: "3", cls: "muted", text: "No disconnects."})]);
    var evRows = Object.create(null);   // keyed by event id: an open offset choice survives the poll
    var evSeen = null;                  // ids seen so far; null until the first poll seeds it
    var suspected = Object.create(null);
    function evRow(ev) {
      var id = ev.id;
      var who = el("td"), what = el("td");
      var confirm = button("Confirm DC", "alarm", null, function () {
        act("/drivers/race/recording/event", {id: id, decision: "confirm"}, "DC confirmed");
      });
      var dismiss = button("Dismiss", "", null, function () {
        act("/drivers/race/recording/event", {id: id, decision: "dismiss"}, "DC dismissed");
      });
      var offset = el("select", {"aria-label": "Seconds before the last packet"},
                      OFFSETS.map(function (n) { return opt(String(n), n + " s before", n === 10); }));
      var showBtn = button("Show standings", "", null, function () {
        var r = evRows[id];
        var at = r.ev.last_packet - parseInt(offset.value, 10);
        window.open(href("/drivers/snapshot?time=" + encodeURIComponent(at.toFixed(1))), "_blank", "noopener");
      });
      var tr = el("tr", {}, [who, what, el("td", {}, [el("div", {cls: "row"}, [confirm, dismiss]),
        el("div", {cls: "row", style: "margin-top:4px"}, [offset, showBtn])])]);
      return evRows[id] = {tr: tr, who: who, what: what, confirm: confirm, dismiss: dismiss, ev: ev};
    }
    // Returns true when the set of suspected entries changed, so the race table re-marks them.
    function renderRecording(rec) {
      var before = Object.keys(suspected).sort().join("\n");
      suspected = Object.create(null);
      var running = !!(rec && rec.running);
      recDot.className = "rec" + (running ? " on" : "");
      recText.textContent = !rec ? "Recording unavailable." : running
        ? "Recording since " + new Date(rec.started_at * 1000).toLocaleTimeString() + " by " + rec.started_by
        : "Not recording";
      recStart.hidden = running;
      recStart.disabled = !rec;
      recStop.hidden = !running;
      recCsv.hidden = recBrowse.hidden = !(rec && rec.span);
      // Keep the last events on screen; the stale note covers a dead relay.
      if (!rec) return before !== "";
      var lost = (rec.dropped || 0) + (rec.failed || 0);
      recWarn.hidden = !lost;
      recWarn.textContent = lost ? "The recording has gaps: " + (rec.dropped || 0) + " samples dropped, " +
        (rec.failed || 0) + " writes failed." : "";
      var events = rec.events || [];
      var open = 0, fresh = false;
      var ids = events.map(function (ev) {
        var r = evRows[ev.id] || evRow(ev);
        r.ev = ev;
        var undecided = ev.state === "suspected" || ev.state === "dropout";
        if (undecided) open++;
        if (ev.state === "suspected") {
          suspected[ev.entry] = true;
          if (evSeen && !evSeen[ev.id]) fresh = true;
        }
        var st = EVENT_STATES[ev.state] || [String(ev.state), ""];
        var label = ev.state === "dropout" && typeof ev.dropout_s === "number"
          ? "Dropout, " + Math.round(ev.dropout_s) + " s" : st[0];
        r.who.replaceChildren(el("div", {text: ev.entry}), el("div", {cls: "muted", text: fitName(ev.driver, NAME_LIST_MAX)}));
        r.what.replaceChildren.apply(r.what, [el("span", {cls: "pill " + st[1], text: label}),
          el("div", {cls: "muted", text: "Last packet " + new Date(ev.last_packet * 1000).toLocaleTimeString()}),
          ev.decided_by ? el("div", {cls: "muted", text: "by " + ev.decided_by}) : null].filter(Boolean));
        r.confirm.hidden = r.dismiss.hidden = !undecided;
        return ev.id;
      });
      evSeen = Object.create(null);
      ids.forEach(function (id) { evSeen[id] = true; });
      if (fresh) beep();
      evTitle.textContent = "Events (" + open + ")";
      // Rows stay in place and only new ones are inserted, so a focused control keeps its focus.
      var rows = ids.map(function (id) { return evRows[id].tr; });
      if (!rows.length) rows = [evEmpty];
      rows.forEach(function (tr, i) {
        if (evBody.children[i] !== tr) evBody.insertBefore(tr, evBody.children[i] || null);
      });
      while (evBody.children.length > rows.length) evBody.removeChild(evBody.lastChild);
      return Object.keys(suspected).sort().join("\n") !== before;
    }

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
    var estEntry = el("select", {"aria-label": "Quiet entry to estimate"});
    var estRef = el("select", {"aria-label": "Reference entry"});
    var estSec = el("input", {type: "number", min: "0", step: "0.1", placeholder: "seconds",
                              "aria-label": "Seconds behind the reference"});
    var estSet = button("Set estimate", "", null, async function () {
      var n = parseFloat(estSec.value);
      if (!isFinite(n) || n < 0) return say("Seconds behind as a number, 0 or more", true);
      var d = await act("/drivers/race/estimate", {entry: estEntry.value, ref: estRef.value, seconds: n},
                        "Estimate set");
      if (d) estSec.value = "";
    });
    var estClear = button("Clear estimate", "", null, function () {
      act("/drivers/race/estimate", {entry: estEntry.value, clear: true}, "Estimate cleared");
    });
    var estList = el("div", {cls: "muted"});
    var retEntry = el("select", {"aria-label": "Entry to retire"});
    var retOn = button("Retire (DNF)", "alarm", "retire", function () {
      act("/drivers/race/retire", {entry: retEntry.value, on: true}, "Retired: " + retEntry.value);
    });
    var retOff = button("Undo DNF", "", null, function () {
      act("/drivers/race/retire", {entry: retEntry.value, on: false}, "Retirement undone");
    });
    // Shown names: one override per entry and registered driver ("" = no sender).
    var nameSrc = {race: [], ready: [], overrides: []};
    var nameEntry = el("select", {"aria-label": "Entry whose driver name to change"});
    var nameDriver = el("select", {"aria-label": "Driver whose name to change"});
    var nameText = el("input", {type: "text", maxlength: "24", placeholder: "shown name", cls: "wide",
                                "aria-label": "Shown driver name"});
    function nameDrivers() {
      var entry = nameEntry.value, out = [];
      function add(d) { if (out.indexOf(d) < 0) out.push(d); }
      nameSrc.race.forEach(function (e) { if (e.entry === entry) add(e.driver_registered || ""); });
      nameSrc.ready.forEach(function (r) {
        if (r.entry === entry) r.senders.forEach(function (s) { add(s.name); });
      });
      nameSrc.overrides.forEach(function (o) { if (o.entry === entry) add(o.driver); });
      if (!out.length) add("");
      return out.map(function (d) { return [d, d || "(no sender)"]; });
    }
    nameEntry.addEventListener("change", function () {
      nameDriver.replaceChildren.apply(nameDriver, nameDrivers().map(function (i) { return opt(i[0], i[1]); }));
    });
    function setName(name) {
      return act("/drivers/race/driver-name", {entry: nameEntry.value, driver: nameDriver.value, name: name},
                 name ? "Name set" : "Name cleared");
    }
    var nameSet = button("Set name", "", null, async function () {
      var name = nameText.value.trim();
      if (!/^[A-Za-z0-9 _.\-]{1,24}$/.test(name)) return say("Name: 1 to 24 letters, digits, spaces, _ . or -", true);
      if (await setName(name)) nameText.value = "";
    });
    var nameClear = button("Clear name", "", null, function () { setName(""); });
    var nameList = el("div", {cls: "muted"});
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
    var flClass = el("select", {"aria-label": "Fastest lap class", hidden: ""});
    var lastRace = {};
    flClass.addEventListener("change", function () { renderFastest(lastRace); });
    var flClear = button("Clear fastest lap", "warn", "fl-clear", function () {
      act("/drivers/race/fastest", fastestBody(flClass.value, {}),
          (flClass.value ? flClass.value + " fastest lap" : "Fastest lap") + " cleared");
    });
    var flEntry = el("select", {"aria-label": "Fastest lap entry"});
    var flDriver = el("input", {type: "text", maxlength: "24", placeholder: "driver", cls: "wide",
                                "aria-label": "Fastest lap driver"});
    var flTime = el("input", {type: "text", placeholder: "1:58.412", "aria-label": "Fastest lap time", size: "9"});
    var flSet = button("Set fastest lap", "", null, async function () {
      var ms = parseLap(flTime.value);
      if (!isFinite(ms) || ms <= 0) return say("Lap time as m:ss.mmm", true);
      var d = await act("/drivers/race/fastest",
                        fastestBody(flClass.value, {entry: flEntry.value, driver: flDriver.value.trim(), ms: ms}),
                        (flClass.value ? flClass.value + " fastest lap" : "Fastest lap") + " set");
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
    // Roster rows too: in qualifying the race has no entries.
    var tyreSrc = {roster: [], race: [], compounds: {}};
    function syncTyres() {
      var known = tyreSrc.roster.slice();
      tyreSrc.race.concat(Object.keys(tyreSrc.compounds)).forEach(function (e) {
        if (known.indexOf(e) < 0) known.push(e);
      });
      renderTyres(known, tyreSrc.compounds);
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
      classLapRow,
      el("div", {cls: "row"}, [redFlag, restart, release]),
      redBanner,
      el("h3", {text: "Race"}),
      el("div", {cls: "row"}, [recDot, recText, recStart, recStop, recCsv, recBrowse]),
      recWarn, raceNote, raceTable,
      el("div", {cls: "row"}, [corrEntry, corrM, corrPit, corrWhy, corrBtn]),
      el("div", {cls: "row"}, [el("span", {cls: "muted", text: "Estimate"}), estEntry,
                               el("span", {cls: "muted", text: "behind"}), estRef, estSec, estSet, estClear]),
      estList,
      el("div", {cls: "row"}, [el("span", {cls: "muted", text: "Retire"}), retEntry, retOn, retOff]),
      el("div", {cls: "row"}, [el("span", {cls: "muted", text: "Driver name"}), nameEntry, nameDriver, nameText,
                               nameSet, nameClear]),
      nameList,
      multiRow,
      evTitle,
      el("div", {cls: "row"}, [el("label", {cls: "check"}, [soundBox, el("span", {cls: "muted", text: "Sound on suspicion"})])]),
      evTable,
      el("h3", {text: "Dashboard"}),
      el("div", {cls: "row"}, [onAir, el("label", {cls: "muted", text: "Delay (s)"}), delay]),
      el("div", {cls: "row"}, [flClass, flText, flClear]),
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

    // Overall, or in class mode one class: the lap shown is the one the form clears.
    function renderFastest(r) {
      lastRace = r;
      var names = flClasses(r);
      flClass.hidden = !names.length;
      keepSelect(flClass, [["", "Overall"]].concat(names.map(function (n) { return [n, n]; })));
      var cls = names.indexOf(flClass.value) >= 0 ? flClass.value : "";
      keepSelect(flEntry, flEntries(r, cls).map(function (e) { return [e, e]; }));
      var fl = scopedFastest(r, cls);
      flText.textContent = fl ? (cls ? cls + " fastest lap " : "Fastest lap ") + lapTime(fl.ms) + " · " +
        fitName(fl.driver, NAME_LIST_MAX) + " (" + fl.entry + ")" : "No fastest lap yet.";
      flClear.disabled = !fl;
    }
    function renderRace(r) {
      var s = r.settings || {};
      onAir.dataset.on = s.visible ? "1" : "";
      onAir.textContent = s.visible ? "Hide dashboard" : "Show dashboard";
      onAir.className = s.visible ? "warn" : "";
      if (document.activeElement !== delay) delay.value = s.delay_s || 0;
      showTyre.box.checked = s.show_compound !== false;
      showFuel.box.checked = s.show_fuel !== false;
      tyreSrc.race = (r.entries || []).map(function (e) { return e.entry; });
      tyreSrc.compounds = r.compounds || {};
      syncTyres();
      renderFastest(r);
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
      nameSrc.race = r.entries || [];
      nameSrc.overrides = r.driver_overrides || [];
      keepSelect(nameDriver, nameDrivers());
      nameList.textContent = nameSrc.overrides.length ? "Name overrides: " + nameSrc.overrides.map(function (o) {
        return o.entry + " " + (o.driver || "(no sender)") + " \u2192 " + o.name;
      }).join(", ") : "";
      var est = (r.entries || []).filter(function (e) { return e.estimate; });
      estList.textContent = est.length ? "Estimated: " + est.map(function (e) {
        return e.entry + " " + e.estimate.seconds + " s behind " + e.estimate.ref +
          (e.status === "est" ? "" : " (waiting for its data to stop)");
      }).join(", ") : "";
      renderClassLaps(r);
      var all = r.entries || [];
      function raceRow(e, grouped, k) {
        var i = all.indexOf(e);
        var st = STATUS[e.status] || [e.status, ""];
        var stPill = {cls: "pill " + st[1], text: st[0]};
        if (e.status === "est" && e.estimate) {
          stPill.title = "Estimated " + e.estimate.seconds + " s behind " + e.estimate.ref + " until its data returns";
        }
        var gap = gapCell(e, grouped, i, k);
        var cells = [el("td", grouped ? {cls: "num", text: String(e.class_pos), title: "P" + (i + 1) + " overall"}
                                      : {cls: "num", text: String(i + 1)}), logoCell(e),
          el("td", {}, [el("div", {text: e.entry}), el("div", {cls: "muted nowrap", text: fitName(e.driver, NAME_MAX),
            title: e.driver + (e.driver_registered && e.driver !== e.driver_registered ? " (registered as " +
                               e.driver_registered + ")" : "")})]),
          el("td", {cls: "num", text: String(e.laps)}),
          el("td", {cls: "num" + (gap.lap ? " lap" : ""), text: gap.text}),
          el("td", {cls: "num", text: secs(intervalTime(e, grouped))}),
          el("td", {}, [el("div", {cls: "pills"}, [suspected[e.entry] ? el("span", {cls: "pill bad", text: "Suspected DC"}) : null,
                        st[0] ? el("span", stPill) : null,
                        e.finished ? el("span", {cls: "pill ok", text: "Finished"}) : null,
                        e.rejoin ? el("span", {cls: "pill amb", text: "Rejoin"}) : null,
                        e.position_mismatch ? el("span", {cls: "pill amb", text: "GT7 P" + e.gt7_position,
                                                           title: "GT7's position disagrees with the distance order"}) : null])]),
          el("td", {cls: "num", text: String(e.pit_stops), title: PIT_HINT}),
          el("td", {cls: "num", text: e.correction_m ? (e.correction_m > 0 ? "+" : "") + e.correction_m + " m" : ""})];
        return el("tr", {}, cells);
      }
      var rows = [];
      classGroups(r).forEach(function (g) {
        if (g.name !== null) {
          var name = el("span", {text: g.name || "Unclassed"});
          if (g.color) name.style.color = g.color;
          rows.push(el("tr", {cls: "cls"}, [el("td", {colspan: "9"}, [name,
            g.lap !== null ? el("span", {cls: "lap", text: "Lap " + g.lap + (g.laps !== null ? " / " + g.laps : "")}) : null])]));
        }
        g.rows.forEach(function (e, k) { rows.push(raceRow(e, g.name !== null, k)); });
      });
      if (!rows.length) rows = [el("tr", {}, [el("td", {colspan: "9", cls: "muted", text: "No driver data yet."})])];
      raceBody.replaceChildren.apply(raceBody, rows);
    }

    function renderSenders(rd) {
      var multi = !!rd.multi_lobby;
      var entries = Array.from(new Set(rd.rows.map(function (r) { return r.entry; })));
      tyreSrc.roster = entries;
      syncTyres();
      keepSelect(corrEntry, entries.map(function (e) { return [e, e]; }));
      keepSelect(estEntry, entries.map(function (e) { return [e, e]; }));
      keepSelect(estRef, entries.map(function (e) { return [e, e]; }));
      keepSelect(retEntry, entries.map(function (e) { return [e, e]; }));
      keepSelect(nameEntry, entries.map(function (e) { return [e, e]; }));
      nameSrc.ready = rd.rows;
      keepSelect(nameDriver, nameDrivers());
      multiRow.hidden = !multi;
      keepSelect(rejoinEntry, entries.map(function (e) { return [e, e]; }));
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
            text: live.length ? live.map(function (x) { return fitName(x.name, NAME_LIST_MAX); }).join(", ")
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
            el("td", {}, [el("div", {text: fitName(s.name, NAME_LIST_MAX), title: s.name}), el("span", {cls: "pill " + (s.live ? "ok" : "bad"),
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
      [raceTable, evTable, readyTable, sendTable].forEach(function (t) { t.classList.toggle("stale", stale); });
    }
    async function refresh() {
      // Both start at once; a slow recording call never holds back the race table.
      var raceCall = call("/drivers/race", undefined, true);
      var recCall = call("/drivers/race/recording", undefined, true);
      var r = await raceCall;
      markStale(!!r);
      if (r) renderRace(r);
      if (renderRecording(await recCall) && r) renderRace(r);
      var rd = await call("/drivers/readiness", undefined, true);
      if (rd) renderSenders(rd);
    }
    async function refreshInvite() {
      var d = await call("/drivers/invite", undefined, true);
      if (!d) return;
      // Drivers reach the relay through the Funnel; the desk's own origin may be the tailnet.
      state.invite = d.active ? (d.base || location.origin) + "/console/drive?i=" + encodeURIComponent(d.id) : null;
      inviteText.textContent = state.invite || "No invite: it starts with the event.";
      copyBtn.disabled = !state.invite;
      var t = await call("/drivers/timing-link", undefined, true);
      if (!t) return;
      var funnel = t.active ? (t.funnel_url || location.origin + "/console/timing?l=" + encodeURIComponent(t.id)) : null;
      state.timing = t.active && t.served === "hosted" && t.hosted_url ? t.hosted_url : funnel;
      var note = "";
      if (t.active && t.hosted_url) {   // the league's timing host (#1161)
        var h = t.host || {};
        note = t.served === "hosted" ? " (hosted, " : " (Funnel; hosted page also live, ";
        note += h.connected ? h.viewers + " viewers on the host, about " +
          Math.round((h.requests_today || 0) + (h.msgs_today || 0) / 20) + " of 100000 requests today"
          : "host not reachable";
        note += (h.quota_low ? "; quota nearly used: viewers stay on Funnel" : "") + ")";
      }
      timingText.textContent = state.timing ? state.timing + note : "No timing link: it starts with the event.";
      timingCopy.disabled = !state.timing;
    }
    refresh(); refreshInvite();
    setInterval(refresh, 2000);
    setInterval(refreshInvite, 15000);
    return {refresh: refresh};
  }
  window.DriverDesk = {mount: mount};
})();
