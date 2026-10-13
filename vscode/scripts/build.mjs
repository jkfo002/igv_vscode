import { build } from 'esbuild';
import { cp, mkdir } from 'node:fs/promises';

await mkdir('dist', { recursive: true });
await build({ entryPoints: ['src/extension.ts'], bundle: true, platform: 'node',
  format: 'cjs', outfile: 'dist/extension.js', external: ['vscode'], sourcemap: false });
await build({ entryPoints: ['src/view.js'], bundle: true, platform: 'browser',
  format: 'iife', outfile: 'dist/view.js', sourcemap: false });
await cp('../remote', 'dist/remote', { recursive: true,
  filter: (path) => !/(?:__pycache__|\.pyc$|[/\\]test[/\\]|[/\\]\.cache)/.test(path) });
await cp('src/view.css', 'dist/view.css');
await cp('node_modules/@novnc/novnc/docs', 'dist/noVNC-docs', { recursive: true });
await cp('../license.txt', 'LICENSE');
