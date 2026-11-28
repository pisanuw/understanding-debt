/** Mounts udebt (src/ui/boot.ts wires the live site or the preview). */
// The light and dark mode switch first: it defines <hw-theme-toggle> before the header is drawn (a byte-for-byte
// copy of claude-built-apps platform/src/theme/hw-theme.js; test/theme.test.ts pins it).
import './lib/hw-theme.js';
import './styles.css';
import { boot } from './ui/boot';

const root = document.getElementById('app');
if (!root) throw new Error('index.html needs <div id="app">');
void boot(root);
