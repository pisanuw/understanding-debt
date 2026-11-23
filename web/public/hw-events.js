/*!
 * hw-events.js: first-party, cookieless analytics for HintWise apps.
 * Source: pisanuw/claude-built-apps platform/src/analytics/hw-events.js.
 * Copy this file unchanged into an app; do not edit the copy.
 *
 *   <script defer src="/hw-events.js" data-app="<slug>"></script>
 *
 * Sends, with navigator.sendBeacon to the platform's app-events function:
 *   pageview  on load and on every in-app path change (history API), with the
 *             referring site's host on the first one;
 *   engaged   seconds the page was visible, when the path changes or the tab
 *             is hidden;
 *   outbound  a click on a link to another site (props.host);
 *   error     the first 3 script errors on a page (props.message, 100 chars);
 *   and whatever the app sends with window.hintwise.track(name, props).
 * Only the pathname is sent, never the query or the hash (links can carry
 * sign-in tokens), and a path segment that looks like an id or a token (a
 * share code, an invite, an email address) is sent as ":id". An error message
 * has quoted text, email addresses and long numbers taken out, since browsers
 * quote the input in some errors. No cookie, no localStorage, no id kept in the browser: the
 * server counts a visitor with a hash that changes every day. Nothing is sent
 * when the browser asks not to be tracked (Global Privacy Control or Do Not
 * Track) or is automated (navigator.webdriver).
 */
(function () {
  'use strict';
  var script = document.currentScript;
  var app = script && script.getAttribute('data-app');
  var endpoint = (script && script.getAttribute('data-endpoint')) || 'https://auth.hintwise.org/functions/v1/app-events';
  if (!app || (window.hintwise && window.hintwise.track)) return;

  var nav = navigator;
  var off = nav.globalPrivacyControl === true || nav.doNotTrack === '1' || window.doNotTrack === '1' || nav.webdriver === true;
  var NAME = /^[a-z][a-z0-9_]{0,39}$/;
  var queue = [];
  var timer = null;
  // A segment that looks like an id or a secret, not a page name. Kept in step
  // with idLike in events.ts, which applies the same rule again on the server.
  function idLike(seg) {
    if (seg.indexOf('@') >= 0) return true;
    if (/^\d{6,}$/.test(seg) || /^[0-9a-f]{16,}$/i.test(seg)) return true;
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(seg)) return true;
    return seg.split(/[-_.~]/).some(function (part) {
      if (part.length < 8 || !/[a-z]/i.test(part) || (part.match(/\d/g) || []).length < 2) return false;
      return (part.match(/[a-z](?=\d)|\d(?=[a-z])/gi) || []).length >= 3;
    });
  }
  function here() {
    return location.pathname
      .split('/')
      .map(function (seg) {
        return seg && idLike(seg) ? ':id' : seg;
      })
      .join('/');
  }
  function redact(m) {
    return String(m)
      .replace(/(["'`])(?:[^"'`\n]{0,300}\1|[^"'`\n]*$)/g, function (m, q) {
        return m.length > 1 && m.charAt(m.length - 1) === q ? q + '…' + q : q + '…';
      })
      .replace(/[^\s@]+@[^\s@]+/g, '…')
      .replace(/\d{6,}/g, '…');
  }
  var path = here();
  var visibleSince = document.visibilityState === 'visible' ? Date.now() : null;
  var engagedMs = 0;
  var errors = 0;

  function send() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    while (queue.length) {
      var body = JSON.stringify({ a: app, e: queue.splice(0, 20) });
      var sent;
      try {
        sent = !!(nav.sendBeacon && nav.sendBeacon(endpoint, new Blob([body], { type: 'text/plain' })));
      } catch {
        sent = false;
      }
      if (!sent) {
        try {
          fetch(endpoint, { method: 'POST', body: body, keepalive: true, mode: 'no-cors', headers: { 'content-type': 'text/plain' } });
        } catch {
          /* offline or blocked: drop it */
        }
      }
    }
  }

  function push(event, now) {
    if (off) return;
    queue.push(event);
    if (now || queue.length >= 10) send();
    else if (!timer) timer = setTimeout(send, 3000);
  }

  function props(x) {
    if (!x || typeof x !== 'object') return undefined;
    var out = {};
    var n = 0;
    for (var k in x) {
      if (!Object.prototype.hasOwnProperty.call(x, k) || n >= 8) continue;
      var v = x[k];
      if (typeof v === 'string') out[k] = v.slice(0, 100);
      else if (typeof v === 'number' || typeof v === 'boolean') out[k] = v;
      else continue;
      n += 1;
    }
    return n ? out : undefined;
  }

  function track(name, x, value) {
    if (typeof name !== 'string' || !NAME.test(name)) return;
    var e = { n: name, p: path };
    var px = props(x);
    if (px) e.x = px;
    if (typeof value === 'number' && isFinite(value)) e.v = value;
    push(e, false);
  }

  function flushEngaged(now) {
    if (visibleSince !== null) {
      engagedMs += Date.now() - visibleSince;
      visibleSince = document.visibilityState === 'visible' ? Date.now() : null;
    }
    var seconds = Math.round(engagedMs / 100) / 10;
    engagedMs = 0;
    if (seconds >= 1) push({ n: 'engaged', p: path, v: Math.min(seconds, 86400) }, now);
  }

  function pageview(first) {
    var e = { n: 'pageview', p: path };
    if (first && document.referrer) {
      try {
        var host = new URL(document.referrer).hostname;
        if (host && host !== location.hostname) e.r = host;
      } catch {
        /* not a URL */
      }
    }
    push(e, false);
  }

  function changed() {
    if (here() === path) return;
    flushEngaged(false);
    path = here();
    errors = 0;
    pageview(false);
  }

  ['pushState', 'replaceState'].forEach(function (m) {
    var orig = history[m];
    if (typeof orig !== 'function') return;
    history[m] = function () {
      var r = orig.apply(this, arguments);
      changed();
      return r;
    };
  });
  window.addEventListener('popstate', changed);

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') {
      flushEngaged(true);
      send();
    } else {
      visibleSince = Date.now();
    }
  });
  window.addEventListener('pagehide', function () {
    flushEngaged(true);
    send();
  });

  document.addEventListener(
    'click',
    function (ev) {
      var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
      if (!a) return;
      try {
        var u = new URL(a.href, location.href);
        if ((u.protocol === 'http:' || u.protocol === 'https:') && u.hostname !== location.hostname) push({ n: 'outbound', p: path, x: { host: u.hostname } }, true);
      } catch {
        /* not a URL */
      }
    },
    true
  );

  window.addEventListener('error', function (ev) {
    if (errors >= 3) return;
    errors += 1;
    track('error', { message: redact((ev && ev.message) || 'error') });
  });

  window.hintwise = window.hintwise || {};
  window.hintwise.track = track;
  pageview(true);
})();
