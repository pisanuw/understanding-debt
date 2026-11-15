/**
 * A few DOM helpers. Text always goes in through textContent (h's string children), never
 * innerHTML: analysis text comes from a model and, for a person's own analysis, from
 * whoever ran the worker.
 */
export type Child = Node | string | number | null | undefined | false;

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string | boolean | undefined> | null = null, ...children: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v === undefined || v === false) continue;
    if (k === 'class') el.className = String(v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children) add(el, c);
  return el;
}

export function add(parent: Node, ...children: Child[]): Node {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    parent.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  }
  return parent;
}

export function clear<T extends Node>(node: T): T {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function fmt(x: number | null | undefined, digits = 2): string {
  return x === null || x === undefined || Number.isNaN(x) ? '-' : x.toFixed(digits);
}

export function badge(text: string, cls = ''): HTMLSpanElement {
  return h('span', { class: `badge ${cls}`.trim() }, text);
}

/** A link that opens on GitHub in a new tab, or plain text when there is no URL. */
export function extLink(href: string | null, text: string, cls = ''): HTMLElement {
  return href ? h('a', { href, target: '_blank', rel: 'noopener noreferrer', class: cls || undefined }, text) : h('span', { class: cls || undefined }, text);
}

export function button(label: string, onClick: (ev: MouseEvent) => void, cls = ''): HTMLButtonElement {
  const b = h('button', { type: 'button', class: cls || undefined }, label);
  b.addEventListener('click', onClick);
  return b;
}

/** Shows a button as busy (disabled, a new label) until `done` is called. */
export function busy(b: HTMLButtonElement, label: string): () => void {
  const was = b.textContent ?? '';
  b.textContent = label;
  b.disabled = true;
  b.classList.add('spin');
  return () => {
    b.textContent = was;
    b.disabled = false;
    b.classList.remove('spin');
  };
}

let toastTimer: ReturnType<typeof setTimeout> | undefined;

/** A short message at the bottom of the page. */
export function toast(message: string, doc: Document = document): void {
  let t = doc.getElementById('toast');
  if (!t) {
    t = h('div', { id: 'toast', class: 'toast', role: 'status' });
    doc.body.appendChild(t);
  }
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    if (t) t.hidden = true;
  }, 4000);
}

/** "15 Nov 2026" from an ISO time, in the reader's locale. */
export function day(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso).slice(0, 10) : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}
