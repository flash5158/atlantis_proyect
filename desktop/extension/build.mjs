import { build } from 'esbuild';
await build({ entryPoints: ['src/extension.ts'], bundle: true, outfile: 'dist/extension.js', external: ['vscode'], platform: 'node', target: 'node20', sourcemap: true, format: 'cjs' });
