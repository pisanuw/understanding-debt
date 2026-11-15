/**
 * The visitor's model mode and own key, read and written exactly as lib/llm.ts does (the same
 * localStorage keys, the same rules; test/prefs.test.ts checks the two agree). They live here
 * too so that the page's main bundle never imports llm.ts, whose Anthropic browser SDK is
 * loaded only when the person uses their own key (model/byok.ts).
 */
export type LlmMode = 'byok' | 'gateway' | 'off';

export const MODE_KEY = 'hintwise.llmMode';
export const BYOK_KEY = 'hintwise.anthropicKey';

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

const memory = new Map<string, string>();
const memoryStore: Store = {
  getItem: (k) => memory.get(k) ?? null,
  setItem: (k, v) => void memory.set(k, String(v)),
  removeItem: (k) => void memory.delete(k),
};

function defaultStorage(): Store {
  try {
    if (typeof localStorage !== 'undefined') return localStorage;
  } catch {
    // access denied (privacy mode): fall through
  }
  return memoryStore;
}

/** A read that a blocked storage answers with null (llm.ts reads it the same way when it works). */
function read(storage: Store, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return memoryStore.getItem(key);
  }
}

function write(storage: Store, key: string, value: string | null): void {
  try {
    if (value === null) storage.removeItem(key);
    else storage.setItem(key, value);
  } catch {
    // blocked or full: kept in memory for this page
    if (value === null) memoryStore.removeItem(key);
    else memoryStore.setItem(key, value);
  }
}

export function getMode(storage: Store = defaultStorage()): LlmMode {
  const v = read(storage, MODE_KEY);
  return v === 'byok' || v === 'gateway' ? v : 'off';
}

export function setMode(mode: LlmMode, storage: Store = defaultStorage()): void {
  write(storage, MODE_KEY, mode);
}

export function getByokKey(storage: Store = defaultStorage()): string | null {
  const v = read(storage, BYOK_KEY);
  return v && v.trim() ? v.trim() : null;
}

export function setByokKey(key: string | null, storage: Store = defaultStorage()): void {
  write(storage, BYOK_KEY, key && key.trim() ? key.trim() : null);
}
