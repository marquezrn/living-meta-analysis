/** Build checked-in, offline reader assets; end users do not need Node.js. */
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { mkdir, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(resolve(root, 'web/package.json'));
const { build } = await import(pathToFileURL(require.resolve('vite')).href);
const { default: react } = await import(pathToFileURL(require.resolve('@vitejs/plugin-react')).href);
const temporary = resolve(tmpdir(), `livingmeta-reader-build-${process.pid}`);
const assets = resolve(root, 'src/livingmeta/report_assets');
await mkdir(assets, { recursive: true });
try {
  await build({ configFile: false, root: resolve(root, 'web'), plugins: [react()],
    define: { 'process.env.NODE_ENV': JSON.stringify('production') },
    build: { outDir: temporary, emptyOutDir: true, sourcemap: false, target: 'es2022', minify: true, cssCodeSplit: false,
      lib: { entry: resolve(root, 'web/report/main.tsx'), name: 'LivingMetaReport', formats: ['iife'], fileName: 'reader' },
    },
  });
  const emitted = await readdir(temporary);
  const javascript = emitted.filter(name => name.endsWith('.js'));
  const stylesheets = emitted.filter(name => name.endsWith('.css'));
  if (javascript.length !== 1 || stylesheets.length !== 1) throw new Error('The reader must contain exactly one script and one stylesheet.');
  let reader = await readFile(resolve(temporary, javascript[0]), 'utf8');
  reader = reader.replace(/<\/script/gi, '<\\/script');
  if (reader.includes('/api/v1') || reader.includes('/auth/github')) throw new Error('Offline reader contains an application-service dependency.');
  if (reader.includes('process.env.NODE_ENV')) throw new Error('The standalone reader must not require a Node.js process object.');
  await writeFile(resolve(assets, 'reader.js'), reader);
  await writeFile(resolve(assets, 'reader.css'), await readFile(resolve(temporary, stylesheets[0])));
  const notices = ['Living Meta-Analysis offline reader', 'Copyright 2026 Ronald Marquez Contreras. MIT License.',
    'Bundled code includes the following dependencies; no research source PDFs are included.'];
  for (const name of ['react', 'react-dom', 'scheduler', 'plotly.js-dist-min', 'lucide-react']) {
    const packageRoot = name === 'scheduler'
      ? dirname(createRequire(await realpath(resolve(root, 'web/node_modules/react-dom/package.json'))).resolve('scheduler/package.json'))
      : resolve(root, 'web/node_modules', name);
    const packageInfo = JSON.parse(await readFile(resolve(packageRoot, 'package.json'), 'utf8'));
    const licenseNames = ['LICENSE', 'LICENSE.txt', 'LICENSE.md'];
    let license;
    for (const filename of licenseNames) {
      try { license = await readFile(resolve(packageRoot, filename), 'utf8'); break; }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    if (!license) throw new Error(`Dependency license is unavailable: ${name}`);
    notices.push(`\n${name} ${packageInfo.version}\n${license}`);
  }
  await writeFile(resolve(assets, 'NOTICES.txt'), notices.join('\n'));
  await writeFile(resolve(assets, 'build.json'), JSON.stringify({ entry: 'web/report/main.tsx', format: 'iife',
    network_services: false, split_chunks: false, plotly: '4.1.1', schema_version: 2 }, null, 2) + '\n');
  console.log('Offline reader assets built with no external chunks or application API routes.');
} finally { await rm(temporary, { recursive: true, force: true }); }
