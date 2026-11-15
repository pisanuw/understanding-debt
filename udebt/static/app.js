/* udebt serve: the teaching surface. Plain DOM, no framework, no request beyond this
   origin. Text from the server is set with textContent; only the tour and decision record
   HTML, which the server renders from Markdown with everything escaped, use innerHTML. */
(function () {
  "use strict";
  var TOKEN = new URLSearchParams(location.search).get("t") || "";
  var S = null; // /api/state
  var loaded = {};
  var tutorSession = null, tutorStarted = 0, tutorTimer = null, tutorRestart = false;

  // -- helpers ------------------------------------------------------------------------
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") e.className = attrs[k]; else e.setAttribute(k, attrs[k]);
    }
    if (text != null) e.textContent = text;
    return e;
  }
  function add(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]); return parent; }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function fmt(x, d) { return x == null ? "-" : Number(x).toFixed(d == null ? 2 : d); }
  function depth(d) { return d == null ? "-" : d + "/5"; }
  function toast(msg) {
    var t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(function () { t.hidden = true; }, 6000);
  }
  function api(path, body) {
    var opts = { headers: { "X-Udebt-Token": TOKEN } };
    if (body !== undefined) {
      opts.method = "POST"; opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) { var err = new Error(data.error || ("HTTP " + r.status)); err.status = r.status; err.data = data; throw err; }
        return data;
      });
    });
  }
  function busy(btn, on, label) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.textContent; btn.textContent = label || "Working"; btn.classList.add("spin"); btn.disabled = true; }
    else { btn.textContent = btn.dataset.label || btn.textContent; btn.classList.remove("spin"); btn.disabled = false; }
  }
  function badge(text, cls) { return el("span", { "class": "badge " + (cls || "") }, text); }
  function unitName(id) {
    var u = (S && S.units || []).filter(function (x) { return x.id === id; })[0];
    return u ? u.name : id;
  }
  function fillUnits(sel, withAny) {
    clear(sel);
    if (withAny) add(sel, el("option", { value: "" }, "Most debt first (your set)"));
    (S.units || []).forEach(function (u) {
      if (u.scope !== "architectural") return;
      add(sel, el("option", { value: u.id }, (u.parent ? "  - " : "") + u.name + " (" + u.id + ")"));
    });
  }
  function confPicker(container, name) {
    clear(container);
    add(container, el("span", { "class": "small muted" }, "Confidence"));
    [["1", "guess"], ["2", "fairly sure"], ["3", "certain"]].forEach(function (c) {
      var lab = el("label");
      var inp = el("input", { type: "radio", name: name, value: c[0] });
      if (c[0] === "2") inp.checked = true;
      add(lab, inp, document.createTextNode(c[1]));
      add(container, lab);
    });
    return function () {
      var x = container.querySelector("input:checked"); return x ? Number(x.value) : 2;
    };
  }

  // -- header and routing -------------------------------------------------------------
  function refreshState() {
    return api("/api/state").then(function (s) {
      S = s;
      $("repo").textContent = s.repo;
      $("who").textContent = s.email + " at " + s.head;
      var b = s.basis;
      clear($("stat"));
      add($("stat"), document.createTextNode("debt index "), el("strong", null, fmt(s.index, 1)),
        document.createTextNode(" · " + b.measured + " measured, " + b.estimated + " estimated" +
          (s.review_due ? " · " + s.review_due + " to review" : "")));
      return s;
    });
  }
  var TABS = ["dashboard", "tour", "path", "unit", "quiz", "review", "digest", "tutor", "teachback"];
  function route() {
    var h = location.hash.replace(/^#/, "") || "path";
    var parts = h.split("/");
    var tab = TABS.indexOf(parts[0]) >= 0 ? parts[0] : "path";
    TABS.forEach(function (t) {
      $("tab-" + t).hidden = t !== tab;
      var a = document.querySelector('[data-tab="' + t + '"]');
      a.setAttribute("aria-selected", t === tab ? "true" : "false");
    });
    var arg = parts.slice(1).join("/");
    if (tab === "unit") showUnit(arg ? decodeURIComponent(arg) : $("unit-pick").value);
    else if (!loaded[tab]) { loaded[tab] = true; (OPEN[tab] || function () {})(); }
    if (tab === "tutor" && arg && arg !== tutorSession) attachTutor(decodeURIComponent(arg));
  }
  // keep the token in the page URL when tabs change the hash
  window.addEventListener("hashchange", route);

  // -- dashboard ----------------------------------------------------------------------
  function openDashboard() {
    var f = $("dash");
    f.src = "/dashboard?t=" + encodeURIComponent(TOKEN);
    function fit() {
      try { var h = f.contentDocument.documentElement.scrollHeight; if (h) f.style.height = (h + 4) + "px"; } catch (e) { /* not loaded yet */ }
    }
    f.addEventListener("load", function () { fit(); setTimeout(fit, 400); setTimeout(fit, 1500); });
    window.addEventListener("message", function (ev) {
      if (ev.origin === location.origin && ev.data && ev.data.udebtHeight) f.style.height = (ev.data.udebtHeight + 4) + "px";
    });
    window.addEventListener("resize", function () { setTimeout(fit, 200); });
  }

  // -- tour ---------------------------------------------------------------------------
  function showTour(t) {
    var body = clear($("tour-body"));
    if (t.markdown) { body.innerHTML = t.html; return; }
    add(body, el("p", null, t.stale ? "The tour was written from an older map; write it again to match the units as they are." :
      "No tour yet. It is written once (one model call, about half a minute) and kept in map.json for the whole team."));
    var b = el("button", { "class": "primary", type: "button" }, "Write the tour");
    b.addEventListener("click", function () {
      busy(b, true, "Writing the tour");
      api("/api/tour", { refresh: !!t.stale }).then(showTour).catch(function (e) { busy(b, false); toast(e.message); });
    });
    add(body, b);
  }
  function openTour() { api("/api/tour").then(showTour).catch(function (e) { toast(e.message); }); }

  // -- path ---------------------------------------------------------------------------
  function openPath() {
    api("/api/path").then(function (p) {
      $("path-sub").textContent = p.steps.length + " units over " + (p.scope === "set" ? "your responsibility set" : "every top-level unit") +
        ", each after the units it uses; among those, the most debt first.";
      var sess = clear($("path-session"));
      add(sess, el("h3", null, "A ten-minute session"));
      if (!p.session.length) add(sess, el("p", null, "Nothing to do now: every unit in view is known to level 3 and current."));
      else add(sess, el("p", null, p.session.map(unitName).join(", then ") + " (about " + p.session_minutes + " minutes)."));
      if (S && S.basis && !S.basis.measured) add(sess, el("p", { "class": "small muted" }, "Nothing measured yet: the Tour is the map to start with."));
      var list = clear($("path-list"));
      p.steps.forEach(function (s, i) {
        var li = el("li", { "class": "pathstep" });
        add(li, el("span", { "class": "num" }, String(i + 1)));
        var body = el("div");
        var name = el("a", { href: "#unit/" + encodeURIComponent(s.unit) }, s.name);
        add(body, add(el("div"), name, document.createTextNode(" "), el("code", null, s.unit)));
        add(body, add(el("div", { "class": "meta" }, ""),
          badge(s.basis, s.basis), document.createTextNode(" und " + fmt(s.understanding) + " · depth " + depth(s.depth) +
            " · debt " + fmt(s.debt) + " · " + s.minutes + " min")));
        if (s.after.length) add(body, el("div", { "class": "meta" }, "uses " + s.after.map(unitName).join(", ")));
        add(body, el("div", { "class": "next" }, "Next: " + s.action));
        var acts = el("div", { "class": "acts" });
        var open = el("button", { type: "button" }, "Open the unit");
        open.addEventListener("click", function () { location.hash = "#unit/" + encodeURIComponent(s.unit); });
        var quiz = el("button", { type: "button" }, "Quiz it");
        quiz.addEventListener("click", function () { $("quiz-unit").value = s.unit; location.hash = "#quiz"; });
        var talk = el("button", { type: "button" }, "Ask the tutor");
        talk.addEventListener("click", function () { $("tutor-unit").value = s.unit; location.hash = "#tutor"; });
        add(acts, open, quiz, talk);
        if (s.due_reviews) { var rv = el("button", { type: "button" }, "Review"); rv.addEventListener("click", function () { location.hash = "#review"; }); add(acts, rv); }
        add(body, acts);
        add(li, body);
        add(list, li);
      });
    }).catch(function (e) { toast(e.message); });
  }

  // -- unit ---------------------------------------------------------------------------
  function excerptInto(box, file, start, end) {
    api("/api/excerpt?file=" + encodeURIComponent(file) + "&start=" + start + "&end=" + end).then(function (x) {
      var pre = el("pre", { "class": "code", tabindex: "0", "aria-label": file + " lines " + x.start + " to " + x.end });
      x.lines.forEach(function (ln, i) {
        var row = el("span", { "class": "l" });
        add(row, el("span", { "class": "n" }, String(x.start + i)), document.createTextNode(ln));
        add(pre, row);
      });
      if (x.cut) add(pre, el("span", { "class": "l muted" }, "      ... " + x.cut + " more line(s) in the range"));
      add(box, pre);
    }).catch(function (e) { add(box, el("p", { "class": "error small" }, e.message)); });
  }
  function walkthroughCard(u) {
    var card = el("div", { "class": "card" });
    add(card, el("h3", null, "Walkthrough: one request through the code"));
    var w = u.walkthrough;
    if (!w) {
      add(card, el("p", null, u.walkthrough_stale ? "The unit's files changed since its walkthrough was written; write it again to see the code as it is." :
        "No walkthrough yet. One model call traces one real request through this unit, step by step, with the actual lines; it is kept for the team."));
      var b = el("button", { "class": "primary", type: "button" }, "Write the walkthrough");
      b.addEventListener("click", function () {
        busy(b, true, "Tracing a request");
        api("/api/walkthrough", { unit: u.id, refresh: !!u.walkthrough_stale }).then(function () { showUnit(u.id, true); })
          .catch(function (e) { busy(b, false); toast(e.message); });
      });
      add(card, b);
      return card;
    }
    add(card, el("p", null, w.flow));
    w.steps.forEach(function (st, i) {
      var box = el("div", { "class": "step" });
      add(box, el("div", { "class": "where" }, (i + 1) + ". " + st.file + ":" + st.start_line + "-" + st.end_line));
      add(box, el("p", null, st.narration));
      excerptInto(box, st.file, st.start_line, st.end_line);
      add(card, box);
    });
    if (w.trap) add(card, add(el("p", { "class": "answer" }), el("strong", null, "The trap"), document.createTextNode(w.trap)));
    add(card, el("p", { "class": "muted small" }, "Written " + String(w.generated_at || "").slice(0, 10) + " at " + String(w.head || "").slice(0, 8) + "; rewritten when a file of the unit changes."));
    return card;
  }
  function showUnit(id, force) {
    if (!S) return;
    var pick = $("unit-pick");
    if (!id) id = pick.value || (S.units[0] && S.units[0].id);
    pick.value = id;
    if (!force && loaded.unit === id) return;
    loaded.unit = id;
    var body = clear($("unit-body"));
    add(body, el("p", { "class": "muted" }, "Loading..."));
    api("/api/unit?id=" + encodeURIComponent(id)).then(function (u) {
      clear(body);
      var sc = u.score;
      add(body, el("h2", null, u.name));
      add(body, el("p", { "class": "sub" }, u.summary));
      var tiles = el("div", { "class": "tiles" });
      function tile(label, value, note, extra) {
        var t = el("div", { "class": "tile" });
        add(t, el("div", { "class": "label" }, label), el("div", { "class": "value" }, value));
        if (extra) add(t, extra);
        if (note) add(t, el("div", { "class": "note" }, note));
        add(tiles, t);
      }
      tile("Understanding", fmt(sc.understanding), sc.basis === "measured" ? "from your answers" + (sc.decay ? ", " + sc.decay : "") : sc.basis === "estimated" ? "estimated from your commits (at most 0.5)" : "nothing yet", badge(sc.basis, sc.basis));
      tile("Depth", depth(sc.depth), "levels passed on the ladder");
      tile("Next", "level " + sc.level, sc.label.replace(/^level \d of 5: /, ""));
      tile("Debt", fmt(sc.debt), "importance " + fmt(sc.importance));
      add(body, tiles);
      var acts = el("div", { "class": "row gap-top" });
      var q = el("button", { "class": "primary", type: "button" }, "Quiz this unit");
      q.addEventListener("click", function () { $("quiz-unit").value = u.id; location.hash = "#quiz"; });
      var t = el("button", { type: "button" }, "Ask the tutor");
      t.addEventListener("click", function () { $("tutor-unit").value = u.id; location.hash = "#tutor"; });
      var tb = el("button", { type: "button" }, "Explain it back");
      tb.addEventListener("click", function () { $("tb-unit").value = u.id; location.hash = "#teachback"; });
      add(body, add(acts, q, t, tb));

      var o = u.orientation;
      var oc = add(el("div", { "class": "card" }), el("h3", null, "Orientation"));
      var dl = el("dl", { "class": "kv" });
      add(dl, el("dt", null, "What it is for"), el("dd", null, o.purpose));
      add(dl, el("dt", null, "The decision"), el("dd", null, o.decision));
      var where = el("dd");
      (o.where || []).forEach(function (w) { add(where, add(el("div"), el("code", null, w.path), document.createTextNode(w.what ? ": " + w.what : ""))); });
      add(dl, el("dt", null, "Where it lives"), where);
      add(dl, el("dt", null, "Who shaped it"), el("dd", null, o.shaped_by));
      var c = u.connections;
      var conn = [];
      if (c.uses.length) conn.push("uses " + c.uses.map(unitName).join(", "));
      if (c.used_by.length) conn.push("used by " + c.used_by.map(unitName).join(", "));
      if (c.changes_with.length) conn.push("changes with " + c.changes_with.map(unitName).join(", "));
      add(dl, el("dt", null, "Connections"), el("dd", null, conn.join("; ") || "none found"));
      add(oc, dl);
      if (o.fallback) add(oc, el("p", { "class": "muted small" }, "From the map entry: the quiz writes a full orientation the first time it asks this unit."));
      add(body, oc);

      add(body, walkthroughCard(u));

      var dc = el("div", { "class": "card prose" });
      dc.innerHTML = u.decision_html;
      add(body, dc);

      var hc = add(el("div", { "class": "card" }), el("h3", null, "Your answers on this unit"));
      if (!u.records.length) add(hc, el("p", { "class": "muted" }, "None yet."));
      var ul = el("ul", { "class": "list" });
      u.records.forEach(function (r) {
        var li = el("li");
        add(li, add(el("div", { "class": "small muted" }), document.createTextNode(String(r.ts).slice(0, 10) + " · level " + (r.level == null ? 5 : r.level) + " · " + (r.source || r.type || "quiz") + " "),
          badge(r.verdict, r.verdict), r.revealed ? document.createTextNode(" · answer shown") : null));
        add(li, el("div", null, r.question));
        add(ul, li);
      });
      add(hc, ul);
      if (u.reviews.length) add(hc, el("p", { "class": "small muted" }, "In review: " + u.reviews.map(function (r) { return "level " + r.level + " due " + String(r.due).slice(0, 10); }).join("; ")));
      add(body, hc);
    }).catch(function (e) { clear(body); add(body, el("p", { "class": "error" }, e.message)); });
  }
  $("unit-pick").addEventListener("change", function () { location.hash = "#unit/" + encodeURIComponent(this.value); });

  // -- the shared question flow (quiz, review) ----------------------------------------
  // A sitting is graded in one call at its end (or at "Finish now"), as the CLI grades a run.
  // "I don't know" needs no call: at levels 1-2 the answer is shown at once.
  function resultCard(q, r) {
    var card = el("div", { "class": "card" });
    add(card, el("p", { "class": "small muted" }, (q.unit_name || unitName(r.unit_id)) + " · level " + r.level));
    add(card, el("p", null, r.question || q.question));
    add(card, add(el("p", { "class": "verdict" }), badge(r.verdict, r.verdict), document.createTextNode(" " + r.feedback)));
    if (r.revealed && r.reference) add(card, add(el("div", { "class": "answer" }), el("strong", null, "The answer"), document.createTextNode(r.reference)));
    add(card, el("p", { "class": "small muted" }, "Next on this unit: level " + r.next_level + (r.review_due ? "; back in review on " + String(r.review_due).slice(0, 10) : "") + "."));
    return card;
  }
  function askFlow(box, questions, done) {
    var i = 0, answers = {}, order = [];
    function finish(btn) {
      var given = order.map(function (id) { return answers[id]; });
      var drop = questions.filter(function (q) { return !answers[q.id]; }).map(function (q) { return q.id; });
      if (!given.length) { clear(box); add(box, el("div", { "class": "card" }, "Nothing answered, nothing recorded.")); return; }
      var typed = given.filter(function (a) { return a.answer && !a.blank; }).length;
      busy(btn, true, typed ? "Grading " + given.length + " answer(s) in one call" : "Recording");
      api("/api/answers", { answers: given, drop: drop }).then(function (r) {
        clear(box);
        var sum = add(el("div", { "class": "card" }), el("h3", null, "Graded"));
        var counts = {};
        r.results.forEach(function (x) { counts[x.verdict] = (counts[x.verdict] || 0) + 1; });
        add(sum, el("p", null, Object.keys(counts).map(function (k) { return counts[k] + " " + k; }).join(", ") + (typed ? " (one grading call)." : " (no call: every answer was blank).")));
        add(box, sum);
        var byId = {}; questions.forEach(function (q) { byId[q.id] = q; });
        r.results.forEach(function (x, k) { add(box, resultCard(byId[order[k]] || {}, x)); });
        refreshState(); loaded.path = false; loaded.unit = null; loaded.digest = false;
        if (done) done(r.results);
      }).catch(function (e) { busy(btn, false); toast(e.message); });
    }
    function next() {
      clear(box);
      if (i >= questions.length) {
        var end = add(el("div", { "class": "card" }), el("h3", null, "All answered"));
        add(end, el("p", null, order.length + " answer(s) to grade together."));
        var go = el("button", { "class": "primary", type: "button" }, "Grade my answers");
        go.addEventListener("click", function () { finish(go); });
        add(end, go); add(box, end); go.focus();
        return;
      }
      var q = questions[i];
      var card = el("div", { "class": "card" });
      add(card, el("p", { "class": "small muted" }, "[" + (i + 1) + "/" + questions.length + "] " + (q.unit_name || unitName(q.unit_id)) + " · " + q.label));
      if (q.orientation) {
        var d = el("details", { open: "" });
        add(d, el("summary", null, "Orientation"));
        var dl = el("dl", { "class": "kv" });
        add(dl, el("dt", null, "What it is for"), el("dd", null, q.orientation.purpose), el("dt", null, "The decision"), el("dd", null, q.orientation.decision),
          el("dt", null, "Who shaped it"), el("dd", null, q.orientation.shaped_by));
        add(d, dl); add(card, d);
      }
      add(card, el("p", { "class": "verdict" }, q.question));
      var ta = el("textarea", { "aria-label": "Your answer", placeholder: "One or two sentences, in plain language." });
      var confBox = el("span", { "class": "conf" });
      var conf = confPicker(confBox, "conf-" + q.id);
      var last = i + 1 >= questions.length;
      var send = el("button", { "class": "primary", type: "button" }, last ? "Answer and grade" : "Next");
      var dunno = el("button", { type: "button" }, "I don't know");
      var stop = el("button", { type: "button" }, "Finish now");
      add(card, ta, add(el("div", { "class": "row gap-top" }), confBox, send, dunno, order.length ? stop : null));
      var out = el("div");
      add(card, out);
      add(box, card);
      ta.focus();
      function keep(a) { if (!answers[q.id]) order.push(q.id); answers[q.id] = a; }
      send.addEventListener("click", function () {
        if (!ta.value.trim()) { ta.focus(); return; }
        keep({ id: q.id, answer: ta.value, confidence: conf() });
        i++;
        if (last) finish(send); else next();
      });
      dunno.addEventListener("click", function () {
        busy(dunno, true, "Noting it"); send.disabled = true; ta.disabled = true; stop.disabled = true;
        api("/api/dontknow", { id: q.id }).then(function (r) {
          keep({ id: q.id, answer: "", confidence: 1, blank: true });
          busy(dunno, false); dunno.hidden = true; send.hidden = true; stop.hidden = true;
          add(out, add(el("p", { "class": "verdict gap-top" }), badge("unknown", "unknown"), document.createTextNode(" A known unknown: no penalty.")));
          if (r.revealed && r.reference) add(out, add(el("div", { "class": "answer" }), el("strong", null, "The answer"), document.createTextNode(r.reference)));
          var nb = el("button", { "class": "primary", type: "button" }, last ? "Grade my answers" : "Next question");
          nb.addEventListener("click", function () { i++; if (last) finish(nb); else next(); });
          add(out, nb); nb.focus();
        }).catch(function (e) { busy(dunno, false); send.disabled = false; ta.disabled = false; stop.disabled = false; toast(e.message); });
      });
      stop.addEventListener("click", function () { finish(stop); });
    }
    next();
  }

  // -- quiz ---------------------------------------------------------------------------
  function openQuiz() {
    $("quiz-note").textContent = "New questions cost one model call for all of them, plus one for orientations the first time; a pooled question, or the other phrasing of one you missed, costs none. Your answers are graded together at the end, in one call (\"I don't know\" needs none, and at levels 1-2 shows the answer at once).";
  }
  $("quiz-go").addEventListener("click", function () {
    var b = this, unit = $("quiz-unit").value;
    busy(b, true, "Writing questions");
    var body = { n: Number($("quiz-n").value) };
    if (unit) body.units = [unit];
    api("/api/questions", body).then(function (r) {
      busy(b, false);
      askFlow($("quiz-flow"), r.questions);
    }).catch(function (e) { busy(b, false); toast(e.message); });
  });

  // -- review -------------------------------------------------------------------------
  function openReview() { $("review-note").textContent = S && S.review_due ? S.review_due + " item(s) due now." : "Nothing due now."; }
  $("review-go").addEventListener("click", function () {
    var b = this;
    busy(b, true, "Loading");
    api("/api/review" + ($("review-all").checked ? "?all=1" : "")).then(function (r) {
      busy(b, false);
      if (!r.items.length) {
        clear($("review-flow"));
        add($("review-flow"), el("div", { "class": "card" }, r.upcoming ? "Nothing due. Next on " + String(r.next_due).slice(0, 10) + " (" + r.upcoming + " item(s) in all)." :
          "Nothing to review: a question comes back here once you miss it."));
        return;
      }
      askFlow($("review-flow"), r.items);
    }).catch(function (e) { busy(b, false); toast(e.message); });
  });

  // -- tutor --------------------------------------------------------------------------
  function tick() {
    if (!tutorSession) { $("tutor-timer").textContent = ""; return; }
    var s = Math.floor((Date.now() - tutorStarted) / 1000);
    $("tutor-timer").textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") + " of " + (S ? S.session_minutes : 10) + ":00";
    $("tutor-stop").hidden = s < (S ? S.session_minutes : 10) * 60;
  }
  function showTutor(v) {
    tutorSession = v.session;
    if (!tutorStarted) tutorStarted = Date.now() - 1000 * (v.elapsed_s || 0);
    var log = clear($("tutor-log"));
    add(log, el("div", { "class": "msg check" }, unitName(v.unit) + " · " + v.label));
    v.turns.forEach(function (t) {
      var m = el("div", { "class": "msg " + t.role + (t.html ? " md" : "") });
      if (t.html) m.innerHTML = t.html; else m.textContent = t.text; // html: escaped by the server
      add(log, m);
    });
    var ck = clear($("tutor-check"));
    if (v.check) {
      var card = el("div", { "class": "card" });
      add(card, el("p", { "class": "small muted" }, "Check question, level " + v.level + " (graded and recorded)"));
      add(card, el("p", { "class": "verdict" }, v.check.question));
      var ta = el("textarea", { "aria-label": "Your answer to the check question" });
      var confBox = el("span", { "class": "conf" });
      var conf = confPicker(confBox, "conf-tutor");
      var send = el("button", { "class": "primary", type: "button" }, "Answer");
      var dunno = el("button", { type: "button" }, "I don't know");
      var out = el("div");
      add(card, ta, add(el("div", { "class": "row gap-top" }), confBox, send, dunno), out);
      function submit(a) {
        busy(send, true, a ? "Grading" : "Recording"); dunno.disabled = true;
        api("/api/tutor/answer", { session: tutorSession, answer: a, confidence: conf() }).then(function (r) {
          busy(send, false); send.hidden = true; dunno.hidden = true;
          add(out, add(el("p", { "class": "verdict gap-top" }), badge(r.verdict, r.verdict), document.createTextNode(" " + r.feedback)));
          if (r.revealed && r.reference) add(out, add(el("div", { "class": "answer" }), el("strong", null, "The answer"), document.createTextNode(r.reference)));
          refreshState(); loaded.path = false; loaded.unit = null;
        }).catch(function (e) { busy(send, false); dunno.disabled = false; toast(e.message); });
      }
      send.addEventListener("click", function () { if (ta.value.trim()) submit(ta.value); });
      dunno.addEventListener("click", function () { submit(""); });
      add(ck, card);
    }
    tick();
  }
  function attachTutor(id) { // a reload keeps the conversation: the session is in the URL
    api("/api/tutor?session=" + encodeURIComponent(id)).then(function (v) {
      $("tutor-unit").value = v.unit; $("tutor-unit").disabled = true; tutorStarted = 0;
      showTutor(v);
      history.replaceState(null, "", location.pathname + location.search + "#tutor/" + v.session);
    }).catch(function (e) { toast(e.message); });
  }
  function savedChoice(v) { // a saved conversation on this unit: resume it or start over
    var ck = clear($("tutor-check"));
    clear($("tutor-log"));
    var card = el("div", { "class": "card" });
    add(card, el("h3", null, "You have a conversation on " + unitName(v.unit)));
    add(card, el("p", { "class": "small muted" }, "Started " + String(v.created).slice(0, 10) + ", last turn " + String(v.active).replace("T", " ").slice(0, 16) + " UTC; " + v.asked + " message(s) from you" + (v.check ? ", a check question waiting" : "") + "."));
    var last = v.turns.filter(function (t) { return t.role === "tutor"; }).slice(-1)[0];
    if (last) add(card, el("p", { "class": "muted small" }, "The tutor last said: " + last.text.slice(0, 220) + (last.text.length > 220 ? "..." : "")));
    var res = el("button", { "class": "primary", type: "button" }, "Resume it");
    res.addEventListener("click", function () { clear(ck); attachTutor(v.session); });
    var over = el("button", { type: "button" }, "Start over");
    over.addEventListener("click", function () { tutorRestart = true; clear(ck); $("tutor-msg").focus(); toast("Your next message starts a new conversation; the old one is replaced."); });
    add(card, add(el("div", { "class": "row" }), res, over));
    add(ck, card);
  }
  function checkSaved() {
    if (tutorSession) return;
    tutorRestart = false;
    var unit = $("tutor-unit").value;
    if (!unit) return;
    api("/api/tutor?unit=" + encodeURIComponent(unit)).then(function (r) {
      if (r.saved && !tutorSession && $("tutor-unit").value === unit) savedChoice(r.saved);
      else if (!tutorSession) clear($("tutor-check"));
    }).catch(function () { /* nothing saved */ });
  }
  function sendTutor(text) {
    var b = $("tutor-send");
    if (!text.trim()) { $("tutor-msg").focus(); return; }
    busy(b, true, "Thinking"); $("tutor-checkme").disabled = true;
    var body = { message: text };
    if (tutorSession) body.session = tutorSession; else { body.unit = $("tutor-unit").value; if (tutorRestart) body.restart = true; }
    api("/api/tutor", body).then(function (v) {
      busy(b, false); $("tutor-checkme").disabled = false; $("tutor-msg").value = "";
      $("tutor-unit").disabled = true; tutorRestart = false;
      showTutor(v);
      history.replaceState(null, "", location.pathname + location.search + "#tutor/" + v.session);
    }).catch(function (e) {
      busy(b, false); $("tutor-checkme").disabled = false;
      if (e.status === 409 && e.data && e.data.saved) savedChoice(e.data.saved); else toast(e.message);
    });
  }
  function openTutor() {
    if (!tutorTimer) tutorTimer = setInterval(tick, 1000);
    checkSaved();
  }
  $("tutor-unit").addEventListener("change", checkSaved);
  $("tutor-send").addEventListener("click", function () { sendTutor($("tutor-msg").value); });
  $("tutor-checkme").addEventListener("click", function () { sendTutor(($("tutor-msg").value.trim() ? $("tutor-msg").value.trim() + " " : "") + "Check me."); });
  $("tutor-new").addEventListener("click", function () {
    tutorSession = null; tutorStarted = 0; $("tutor-unit").disabled = false;
    history.replaceState(null, "", location.pathname + location.search + "#tutor");
    clear($("tutor-log")); clear($("tutor-check")); $("tutor-stop").hidden = true; tick();
    tutorRestart = true; // "New session" on this unit replaces its saved conversation
  });

  // -- teach-back ---------------------------------------------------------------------
  var tbConf = null;
  var TB_ASKS = {
    1: "what it is for and where it sits in the system",
    2: "what it is for, the decision behind it, and what was not chosen",
    3: "the decision behind it and what else in the system would have to change if it were reversed",
    4: "how it behaves when one of its parts fails, times out or gets bad input",
    5: "how its code does its job: the main path through it and why it is written that way"
  };
  function tbLevel() {
    var u = (S.units || []).filter(function (x) { return x.id === $("tb-unit").value; })[0];
    return u ? u.level : 1;
  }
  function tbPrompt() {
    var n = tbLevel();
    $("tb-prompt").textContent = "Explain " + unitName($("tb-unit").value) + " in your own words: " + TB_ASKS[n] + ". Graded at your level on this unit, level " + n + " (" + S.ladder[String(n)] + ").";
  }
  function openTeachback() { tbConf = confPicker($("tb-conf"), "conf-tb"); tbPrompt(); }
  $("tb-unit").addEventListener("change", tbPrompt);
  $("tb-go").addEventListener("click", function () {
    var b = this, text = $("tb-text").value.trim();
    if (!text) { $("tb-text").focus(); return; }
    busy(b, true, "Checking it against the code");
    api("/api/teachback", { unit: $("tb-unit").value, explanation: text, confidence: tbConf ? tbConf() : 2 }).then(function (r) {
      busy(b, false);
      var out = clear($("tb-result"));
      add(out, add(el("p", { "class": "verdict gap-top" }), badge(r.verdict, r.verdict), document.createTextNode(" " + r.label)));
      var dl = el("dl", { "class": "kv" });
      add(dl, el("dt", null, "What was right"), el("dd", null, r.right || "-"));
      add(dl, el("dt", null, "What was missing"), el("dd", null, r.missing || "nothing important"));
      add(dl, el("dt", null, "Misconception"), el("dd", null, r.misconception || "none found"));
      add(out, dl);
      if (r.revealed && r.reference) add(out, add(el("div", { "class": "answer" }), el("strong", null, "The answer"), document.createTextNode(r.reference)));
      refreshState().then(tbPrompt); loaded.path = false; loaded.unit = null; loaded.digest = false;
    }).catch(function (e) { busy(b, false); toast(e.message); });
  });

  // -- digest -------------------------------------------------------------------------
  var DECISION = { held: "Decision held: the changes work within it.", extended: "Decision extended: it stands and now covers more.", changed: "Decision changed: the choice itself moved." };
  function digestCard(it) {
    var card = el("div", { "class": "card" });
    var h = add(el("h3"), el("a", { href: "#unit/" + encodeURIComponent(it.unit) }, it.name), document.createTextNode(" "), el("code", null, it.unit));
    add(card, h);
    add(card, add(el("p", { "class": "small muted" }), it.basis ? badge(it.basis, it.basis) : null,
      document.createTextNode(" und " + fmt(it.understanding) + " · " + it.range.note + " · " + it.commits.length + " commit(s), mass " + fmt(it.mass))));
    var d = it.digest;
    if (d) {
      add(card, el("p", { "class": "headline" }, d.headline));
      var ul = el("ul", { "class": "list" });
      d.changes.forEach(function (c) { add(ul, add(el("li"), document.createTextNode(c.what + " "), c.commits.length ? el("code", null, c.commits.join(", ")) : null)); });
      add(card, ul);
      add(card, add(el("p", { "class": "answer" }), el("strong", null, DECISION[d.decision] || d.decision), document.createTextNode(d.decision_note)));
      if (d.look_at.length) add(card, add(el("p", { "class": "small" }), document.createTextNode("Look at: "), el("code", null, d.look_at.join(", "))));
    } else {
      add(card, el("p", { "class": "muted" }, "No briefing written yet for this range."));
    }
    var det = el("details");
    add(det, el("summary", null, "The commits"));
    var cl = el("ul", { "class": "list small" });
    it.commits.slice().sort(function (a, b) { return b.mass - a.mass; }).forEach(function (c) {
      add(cl, add(el("li"), el("code", null, c.sha.slice(0, 8)), document.createTextNode(" " + c.date + " · " + c.who + " · " + c.klass + (c.agent ? "/" + c.agent : "") + " · weight " + fmt(c.weight) + ", mass " + fmt(c.mass) + " · " + c.subject)));
    });
    add(det, cl); add(card, det);
    var acts = el("div", { "class": "acts row gap-top" });
    var open = el("button", { type: "button" }, "Open the unit");
    open.addEventListener("click", function () { location.hash = "#unit/" + encodeURIComponent(it.unit); });
    var quiz = el("button", { type: "button" }, "Quiz it");
    quiz.addEventListener("click", function () { $("quiz-unit").value = it.unit; location.hash = "#quiz"; });
    add(card, add(acts, open, quiz));
    return card;
  }
  function showDigest(r) {
    var box = clear($("digest-body"));
    var moved = r.items.filter(function (it) { return it.commits.length; });
    var quiet = r.items.filter(function (it) { return !it.commits.length; });
    var since = $("digest-since").value.trim();
    $("digest-sub").textContent = r.items.length ? moved.length + " of " + r.items.length + " unit(s) " + (r.scope === "set" ? "in your set " : "") + (r.items.length === 1 && $("digest-unit").value ? "" : "below " + r.threshold + " ") + "have changes " + (since ? "since " + since + "." : "since your last answer there (or the last 30 days).") :
      "Every unit in view is above " + r.threshold + ": nothing to catch up on.";
    if (r.missing) {
      var c = el("div", { "class": "card" });
      add(c, el("p", null, r.missing + " briefing(s) not written yet. One model call writes them all; they are cached for anyone with the same range."));
      var b = el("button", { "class": "primary", type: "button" }, "Write the briefings");
      b.addEventListener("click", function () {
        busy(b, true, "Reading what landed");
        api("/api/digest", digestQuery(true)).then(showDigest).catch(function (e) { busy(b, false); toast(e.message); });
      });
      add(c, b); add(box, c);
    }
    moved.forEach(function (it) { add(box, digestCard(it)); });
    if (quiet.length) add(box, el("p", { "class": "small muted" }, "Nothing landed since your start point in " + quiet.map(function (it) { return it.name; }).join(", ") + ": what you knew still holds."));
  }
  function digestQuery(asBody) {
    var since = $("digest-since").value.trim(), unit = $("digest-unit").value;
    if (asBody) { var b = {}; if (since) b.since = since; if (unit) b.unit = unit; return b; }
    var q = []; if (since) q.push("since=" + encodeURIComponent(since)); if (unit) q.push("unit=" + encodeURIComponent(unit));
    return q.length ? "?" + q.join("&") : "";
  }
  function openDigest() {
    clear($("digest-body")); add($("digest-body"), el("p", { "class": "muted" }, "Loading..."));
    api("/api/digest" + digestQuery(false)).then(showDigest).catch(function (e) { clear($("digest-body")); toast(e.message); });
  }
  $("digest-go").addEventListener("click", openDigest);

  var OPEN = { dashboard: openDashboard, tour: openTour, path: openPath, quiz: openQuiz, review: openReview, digest: openDigest, tutor: openTutor, teachback: openTeachback };

  refreshState().then(function () {
    fillUnits($("unit-pick"), false);
    fillUnits($("quiz-unit"), true);
    fillUnits($("tutor-unit"), false);
    fillUnits($("tb-unit"), false);
    fillUnits($("digest-unit"), true);
    $("digest-unit").options[0].textContent = "Units below the threshold (your set)";
    route();
  }).catch(function (e) {
    add(clear($("main")), el("p", { "class": "error" }, "Could not reach udebt serve: " + e.message + ". Open the URL it printed (it carries the token)."));
  });
})();
