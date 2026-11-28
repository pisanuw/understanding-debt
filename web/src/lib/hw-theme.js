/*!
 * hw-theme.js: the light and dark mode switch for HintWise apps.
 * Source: pisanuw/claude-built-apps platform/src/theme/hw-theme.js.
 * Copy this file unchanged into an app; do not edit the copy (README.md
 * beside the source has the snippets and the sha256 to pin it).
 *
 *   <hw-theme-toggle></hw-theme-toggle>        in the header, where the button goes
 *   <script defer src="/hw-theme.js"></script> on a plain page, or
 *   import './hw-theme.js';                    first thing in a bundled app
 *
 * The page's theme is data-theme="light" or "dark" on <html>. theme-init.js,
 * inlined at the top of <head>, sets it before the first paint; this script
 * sets it too if that snippet is missing. The first visit follows the device
 * (prefers-color-scheme), and keeps following it while no choice is stored.
 * A click stores the choice in localStorage ("hw-theme") on this device; a
 * blocked store keeps it for this page only.
 *
 * <hw-theme-toggle> draws a real <button> in an open shadow root: round,
 * outlined, 44 by 44 px, a moon in light mode and a sun in dark mode, named
 * "Switch to dark mode" or "Switch to light mode". It takes the text colour
 * around it. Custom properties --hw-theme-toggle-size, -border, -background
 * and -hover, and ::part(button), restyle it. Its style is a constructed
 * stylesheet, which a strict Content-Security-Policy allows (no hash needed).
 *
 * window.hintwise.theme.get() returns "light" or "dark"; set("light" | "dark")
 * stores a choice; set(null) forgets it and follows the device; toggle()
 * flips it. Every change fires "hw-themechange" on document, with
 * event.detail.theme, for code that draws its own colours (a canvas).
 */
(function () {
  'use strict';
  var KEY = 'hw-theme';
  var EVENT = 'hw-themechange';
  var TAG = 'hw-theme-toggle';
  var SVG_NS = 'http://www.w3.org/2000/svg';
  if (window.hintwise && window.hintwise.theme) return;

  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  var toggles = new Set();

  function valid(t) {
    return t === 'light' || t === 'dark';
  }
  function stored() {
    try {
      var t = localStorage.getItem(KEY);
      return valid(t) ? t : null;
    } catch {
      return null;
    }
  }
  function device() {
    return media && media.matches ? 'dark' : 'light';
  }

  // The visitor's choice, kept in memory too, so a blocked store still
  // holds it for this page and the device setting does not undo it.
  var choice = stored();

  function get() {
    var t = root.getAttribute('data-theme');
    return valid(t) ? t : choice || device();
  }
  function apply(t) {
    var before = root.getAttribute('data-theme');
    root.setAttribute('data-theme', t);
    toggles.forEach(paint);
    if (before !== t) document.dispatchEvent(new CustomEvent(EVENT, { detail: { theme: t } }));
  }
  function set(t) {
    if (t !== null && !valid(t)) throw new TypeError('hw-theme: set("light"), set("dark") or set(null)');
    choice = t;
    try {
      if (t) localStorage.setItem(KEY, t);
      else localStorage.removeItem(KEY);
    } catch {
      // Storage blocked: the choice lasts for this page only.
    }
    apply(t || device());
  }
  function toggle() {
    set(get() === 'dark' ? 'light' : 'dark');
  }
  function resync() {
    choice = stored();
    apply(choice || device());
  }

  if (!valid(root.getAttribute('data-theme'))) apply(choice || device());
  if (media) {
    var onDevice = function () {
      if (!choice) apply(device());
    };
    if (media.addEventListener) media.addEventListener('change', onDevice);
    else if (media.addListener) media.addListener(onDevice);
  }
  // Another tab of the same app changed it, or the page came back from the
  // back-forward cache after another page of the app changed it.
  window.addEventListener('storage', function (e) {
    if (e.key === KEY || e.key === null) resync();
  });
  window.addEventListener('pageshow', function (e) {
    if (e.persisted) resync();
  });

  window.hintwise = window.hintwise || {};
  window.hintwise.theme = { get: get, set: set, toggle: toggle };

  // ---- the button ----

  var CSS =
    ':host{display:inline-flex;flex:none;vertical-align:middle}' +
    ':host([hidden]){display:none}' +
    'button{box-sizing:border-box;display:inline-flex;align-items:center;justify-content:center;' +
    'width:var(--hw-theme-toggle-size,44px);height:var(--hw-theme-toggle-size,44px);margin:0;padding:0;' +
    'border:1px solid var(--hw-theme-toggle-border,currentColor);border-radius:50%;' +
    'background:var(--hw-theme-toggle-background,transparent);color:inherit;font:inherit;line-height:0;' +
    'cursor:pointer;-webkit-tap-highlight-color:transparent}' +
    '@supports (color:color-mix(in srgb,red 50%,blue)){' +
    'button{border-color:var(--hw-theme-toggle-border,color-mix(in srgb,currentColor 60%,transparent))}' +
    'button:hover{background:var(--hw-theme-toggle-hover,color-mix(in srgb,currentColor 10%,transparent))}}' +
    'button:focus-visible{outline:2px solid currentColor;outline-offset:2px}' +
    'svg{display:block;width:20px;height:20px}' +
    '@media (forced-colors:active){button{border-color:ButtonText}}' +
    '@media print{:host{display:none}}';

  // Sun (shown in dark mode) and moon (shown in light mode), drawn with
  // currentColor.
  var SUN = [
    ['circle', { cx: '12', cy: '12', r: '4' }],
    ['path', { d: 'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4' }],
  ];
  var MOON = [['path', { d: 'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z' }]];

  function icon(shapes) {
    var svg = document.createElementNS(SVG_NS, 'svg');
    var attrs = { viewBox: '0 0 24 24', width: '20', height: '20', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false' };
    Object.keys(attrs).forEach(function (k) {
      svg.setAttribute(k, attrs[k]);
    });
    shapes.forEach(function (s) {
      var el = document.createElementNS(SVG_NS, s[0]);
      Object.keys(s[1]).forEach(function (k) {
        el.setAttribute(k, s[1][k]);
      });
      svg.appendChild(el);
    });
    return svg;
  }

  var sheet = null;
  function style(shadow) {
    if ('adoptedStyleSheets' in shadow && typeof CSSStyleSheet === 'function' && 'replaceSync' in CSSStyleSheet.prototype) {
      try {
        if (!sheet) {
          sheet = new CSSStyleSheet();
          sheet.replaceSync(CSS);
        }
        shadow.adoptedStyleSheets = [sheet];
        return;
      } catch {
        sheet = null;
      }
    }
    // Older browsers: a <style> element (a strict CSP drops it; the button
    // still works, unstyled).
    var el = document.createElement('style');
    el.textContent = CSS;
    shadow.appendChild(el);
  }

  function paint(host) {
    var button = host.shadowRoot.querySelector('button');
    var t = get();
    var label = t === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
    if (button.getAttribute('aria-label') === label) return;
    button.setAttribute('aria-label', label);
    button.setAttribute('title', label);
    button.setAttribute('data-shows', t === 'dark' ? 'sun' : 'moon');
    button.replaceChildren(icon(t === 'dark' ? SUN : MOON));
  }

  if (window.customElements && window.HTMLElement && !window.customElements.get(TAG)) {
    window.customElements.define(
      TAG,
      class extends HTMLElement {
        connectedCallback() {
          if (!this.shadowRoot) {
            var shadow = this.attachShadow({ mode: 'open' });
            style(shadow);
            var button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('part', 'button');
            button.addEventListener('click', toggle);
            shadow.appendChild(button);
          }
          toggles.add(this);
          paint(this);
        }
        disconnectedCallback() {
          toggles.delete(this);
        }
      },
    );
  }
})();
