/**
 * The sign-in panel's styles (src/lib/auth.ts, mountSignIn), one CSS block
 * that mountSignIn adds to the page once, first in <head> so the app's own
 * styles can override the layout.
 *
 * The Google button is a custom button under Google's branding guidelines
 * (BUILD_ROUTINE.md, "The usual architecture", "Google sign-in button"):
 * the text "Continue with Google"; the standard multicolour G from Google's
 * signin-assets.zip, unmodified (google-g-light.svg); the light theme (fill
 * #FFFFFF, 1px inside stroke #747775, text #1F1F1F); Google Sans Medium,
 * 14px on a 20px line; 12px before the logo, 10px after it, 12px after the
 * text; pill shape. The template loads no external fonts, so 'Google Sans'
 * is used only where it is installed and Roboto or Arial stand in; an app
 * that already loads Google Fonts may add Google Sans there.
 *
 * The magic-link button shares .hw-signin-button: the same height, type,
 * border and weight, so neither option is more prominent. Light theme only:
 * a dark page needs the dark values (#131314, stroke #8E918F, text #E3E3E3)
 * and the dark asset from the same zip.
 */

export const SIGN_IN_STYLE_ID = 'hw-signin-styles';

export const SIGN_IN_CSS = `
.hw-signin {
  display: grid;
  gap: 0.75rem;
  max-width: 24rem;
}
.hw-signin form {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.hw-signin input[type='email'] {
  flex: 1 1 12rem;
  min-width: 0;
  box-sizing: border-box;
  height: 40px;
  padding: 0 12px;
  font: inherit;
  border: 1px solid #747775;
  border-radius: 20px;
}
.hw-signin-button {
  display: inline-flex;
  justify-self: start;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  height: 40px;
  padding: 0 12px;
  background: #FFFFFF;
  border: 1px solid #747775;
  border-radius: 20px;
  color: #1F1F1F;
  font-family: 'Google Sans', Roboto, Arial, sans-serif;
  font-weight: 500;
  font-size: 14px;
  line-height: 20px;
  white-space: nowrap;
  cursor: pointer;
}
.hw-signin-button:focus-visible {
  outline: 2px solid #0B57D0;
  outline-offset: 2px;
}
.hw-signin-button:disabled {
  cursor: default;
  opacity: 0.6;
}
.hw-signin-google-logo {
  display: inline-flex;
  flex: none;
  width: 20px;
  height: 20px;
  margin-right: 10px;
}
.hw-signin-google-logo svg {
  display: block;
  width: 20px;
  height: 20px;
}
.hw-signin-challenge:empty {
  display: none;
}
`;

/** Adds the panel's styles to the document once. */
export function ensureSignInStyles(doc: Document = document): void {
  if (doc.getElementById(SIGN_IN_STYLE_ID)) return;
  const style = doc.createElement('style');
  style.id = SIGN_IN_STYLE_ID;
  style.textContent = SIGN_IN_CSS;
  doc.head.prepend(style);
}
