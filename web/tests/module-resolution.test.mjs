import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import ts from 'typescript';

const webRoot = fileURLToPath(new URL('..', import.meta.url));
const config = ts.readConfigFile(path.join(webRoot, 'tsconfig.json'), ts.sys.readFile);
assert.equal(config.error, undefined);
const converted = ts.convertCompilerOptionsFromJson(config.config.compilerOptions, '/project');
assert.deepEqual(converted.errors, []);

// Use TypeScript's real resolver with both filesystem policies, even on Linux.
// This catches extensionless imports that choose .ts before the intended .tsx.
function resolve(specifier, importer, files, caseSensitive) {
  const key = name => caseSensitive ? name : name.toLowerCase();
  const index = new Map(files.map(name => [key(name), name]));
  const host = {
    useCaseSensitiveFileNames: caseSensitive,
    fileExists: name => index.has(key(name)),
    readFile: name => index.has(key(name)) ? '' : undefined,
  };
  const result = ts.resolveModuleName(specifier, importer, converted.options, host).resolvedModule;
  return result ? index.get(key(result.resolvedFileName)) : undefined;
}

function sourceFiles(directory, relative = 'src') {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const name = `${relative}/${entry.name}`;
    if (entry.isDirectory()) return sourceFiles(path.join(directory, entry.name), name);
    return /\.(?:[cm]?ts|tsx|[cm]?js|jsx)$/.test(name) ? [name] : [];
  });
}

const sources = sourceFiles(path.join(webRoot, 'src'));
const virtualFiles = sources.map(name => `/project/${name}`);

function moduleCollisions(files) {
  const stems = new Map();
  for (const file of files) {
    // Declaration/implementation pairs are legitimate; executable .ts/.tsx
    // modules with a case-folded shared stem are not safe extensionless targets.
    if (/\.d\.[cm]?ts$/.test(file)) continue;
    const stem = file.replace(/\.(?:[cm]?ts|tsx|[cm]?js|jsx)$/, '').toLowerCase();
    stems.set(stem, [...(stems.get(stem) || []), file]);
  }
  return [...stems.values()].filter(group => group.length > 1);
}

test('regression fixture reproduces Windows .ts shadowing a differently cased .tsx', () => {
  const importer = '/project/src/features/Settings.tsx';
  const files = [importer, '/project/src/features/ModelParameters.tsx', '/project/src/features/modelParameters.ts'];
  assert.equal(resolve('./ModelParameters', importer, files, true), files[1]);
  assert.equal(resolve('./ModelParameters', importer, files, false), files[2]);
  assert.deepEqual(moduleCollisions(files), [[files[1], files[2]]]);
});

test('frontend module stems do not collide on case-insensitive filesystems', () => {
  assert.deepEqual(moduleCollisions(sources), [], 'Rename modules; do not disable casing/type checks.');
});

test('all relative frontend imports resolve identically with either filesystem policy', () => {
  let checked = 0;
  for (const source of sources) {
    const text = readFileSync(path.join(webRoot, source), 'utf8');
    for (const { fileName: specifier } of ts.preProcessFile(text, true, true).importedFiles) {
      if (!specifier.startsWith('.')) continue;
      const importer = `/project/${source}`;
      const sensitive = resolve(specifier, importer, virtualFiles, true);
      const insensitive = resolve(specifier, importer, virtualFiles, false);
      // CSS/assets are handled by Vite, not by the TS source-module resolver.
      if (!sensitive && !insensitive && /\.(?:css|svg|png|jpe?g|webp|json)(?:\?.*)?$/.test(specifier)) continue;
      assert.ok(sensitive, `${source}: unresolved or incorrectly cased import ${specifier}`);
      assert.equal(insensitive, sensitive, `${source}: ${specifier} selects different modules on Windows and Linux`);
      checked += 1;
    }
  }
  assert.ok(checked > 0, 'The regression must inspect actual frontend imports.');
});

// Inspect Vite's real output graph, not import strings or a single entry file.
// A vendor split alone must not disguise an oversized initial download.
test('production chunks defer optional panels and respect the default size budget', async () => {
  const { build } = await import('vite');
  const result = await build({ root: webRoot, logLevel: 'silent', build: { write: false } });
  const chunks = (Array.isArray(result) ? result : [result])
    .flatMap(output => output.output).filter(output => output.type === 'chunk');
  const byName = new Map(chunks.map(chunk => [chunk.fileName, chunk]));
  const initial = new Set();
  function visit(fileName) {
    if (initial.has(fileName)) return;
    const chunk = byName.get(fileName);
    assert.ok(chunk, `Missing emitted static import: ${fileName}`);
    initial.add(fileName);
    chunk.imports.forEach(visit);
  }
  const entries = chunks.filter(chunk => chunk.isEntry);
  assert.ok(entries.length, 'The real application entry must be present.');
  entries.forEach(chunk => visit(chunk.fileName));
  const bytes = chunk => Buffer.byteLength(chunk.code, 'utf8');
  const initialBytes = [...initial].reduce((sum, name) => sum + bytes(byName.get(name)), 0);
  assert.ok(initialBytes <= 500_000, `Initial JS including static dependencies is ${initialBytes} bytes.`);
  for (const chunk of chunks) {
    assert.ok(bytes(chunk) <= 500_000, `${chunk.fileName} exceeds Vite's default 500 kB warning budget.`);
  }
  const deferred = new Set(chunks.flatMap(chunk => chunk.dynamicImports));
  for (const feature of ['Settings', 'SimulationWorkbench', 'FBDPanel', 'HardwarePanel']) {
    const owners = chunks.filter(chunk => Object.keys(chunk.modules).some(id =>
      id.replaceAll('\\', '/').endsWith(`/src/features/${feature}.tsx`)));
    assert.ok(owners.length, `${feature} must remain in the production build.`);
    assert.ok(owners.every(chunk => !initial.has(chunk.fileName)), `${feature} leaked into the initial graph.`);
    assert.ok(owners.some(chunk => deferred.has(chunk.fileName)), `${feature} must have an on-demand entry.`);
  }
});
