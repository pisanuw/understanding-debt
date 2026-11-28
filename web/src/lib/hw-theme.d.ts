// Types for hw-theme.js. A TypeScript app copies this file beside its copy of
// hw-theme.js (any name ending in .d.ts that its tsconfig includes).
// HintwiseGlobal is an interface, so an app that also types
// window.hintwise.track (hw-events.js) merges with it.
export {};

declare global {
  type HwTheme = 'light' | 'dark';

  interface HwThemeApi {
    /** The page's theme now. */
    get(): HwTheme;
    /** Store a choice on this device; null forgets it and follows the device. */
    set(theme: HwTheme | null): void;
    /** Switch to the other theme and store it. */
    toggle(): void;
  }

  interface HintwiseGlobal {
    theme?: HwThemeApi;
  }

  interface Window {
    hintwise?: HintwiseGlobal;
  }

  interface DocumentEventMap {
    'hw-themechange': CustomEvent<{ theme: HwTheme }>;
  }

  interface HTMLElementTagNameMap {
    'hw-theme-toggle': HTMLElement;
  }
}
