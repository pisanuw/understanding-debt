/**
 * The tour's Markdown as DOM nodes: headings, paragraphs, lists, horizontal rules, and
 * **bold**, *italic* and `code` inline. Everything is text: no HTML in the source is ever
 * interpreted, and links are not made.
 */
import { h } from './dom';

function inline(text: string, parent: HTMLElement): void {
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) parent.append(text.slice(last, at));
    const tok = m[0];
    if (tok.startsWith('**')) parent.append(h('strong', null, tok.slice(2, -2)));
    else if (tok.startsWith('`')) parent.append(h('code', null, tok.slice(1, -1)));
    else parent.append(h('em', null, tok.slice(1, -1)));
    last = at + tok.length;
  }
  if (last < text.length) parent.append(text.slice(last));
}

export function renderMarkdown(md: string): DocumentFragment {
  const frag = document.createDocumentFragment();
  const lines = String(md).replace(/\r\n?/g, '\n').split('\n');
  let para: string[] = [];
  let list: HTMLElement | null = null;
  const flush = () => {
    if (para.length) {
      const p = h('p');
      inline(para.join(' '), p);
      frag.append(p);
      para = [];
    }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const head = /^(#{1,4})\s+(.*)$/.exec(line);
    const ol = /^\s*\d+\.\s+(.*)$/.exec(line);
    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    if (!line.trim()) {
      flush();
      list = null;
    } else if (head) {
      flush();
      list = null;
      const level = Math.min(4, head[1].length + 1) as 2 | 3 | 4;
      const el = h(`h${level}` as 'h2');
      inline(head[2], el);
      frag.append(el);
    } else if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
      flush();
      list = null;
      frag.append(h('hr'));
    } else if (ol || ul) {
      flush();
      const tag = ol ? 'ol' : 'ul';
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = h(tag);
        frag.append(list);
      }
      const li = h('li');
      inline((ol ?? ul)?.[1] ?? '', li);
      list.append(li);
    } else if (list && /^\s{2,}\S/.test(raw)) {
      const li = list.lastElementChild as HTMLElement;
      li.append(' ');
      inline(line.trim(), li);
    } else {
      list = null;
      para.push(line.trim());
    }
  }
  flush();
  return frag;
}
