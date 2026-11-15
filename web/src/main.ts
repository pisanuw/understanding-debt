/** Mounts udebt (src/ui/boot.ts wires the live site or the preview). */
import './styles.css';
import { boot } from './ui/boot';

const root = document.getElementById('app');
if (!root) throw new Error('index.html needs <div id="app">');
void boot(root);
