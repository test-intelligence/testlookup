// Validate every ```mermaid code block in the repo's markdown files.
//
// Mermaid diagrams that fail to parse render as "Unable to render rich display"
// on GitHub. This script extracts every fenced ```mermaid block and runs it
// through mermaid.parse() — the same grammar GitHub uses — so a broken diagram
// fails CI instead of silently shipping a broken doc.
//
// Usage:  node validate.mjs [repoRoot]
//   repoRoot defaults to two levels up (repo root, since this lives in
//   scripts/mermaid-check/). Exits non-zero if any diagram fails to parse.

import { JSDOM } from 'jsdom';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { execFileSync } from 'child_process';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(process.argv[2] || path.join(SCRIPT_DIR, '..', '..'));
const SKIP_DIRS = new Set([
  '.git', 'node_modules', '.venv', 'venv', 'venv311', '.venv311',
  'dist', 'build', 'target', '.pytest_cache', '.mypy_cache', '.ruff_cache',
  'htmlcov', '.next', 'coverage',
]);

// ── Set up a minimal DOM so mermaid can initialise headlessly ───────────────
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
global.DOMParser = dom.window.DOMParser;
global.Node = dom.window.Node;

const mermaid = (await import('mermaid')).default;
mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });

// ── Collect markdown files ──────────────────────────────────────────────────
// Prefer git-tracked files so local runs match the CI checkout exactly (the
// gitignored docs/ tree, vendored node_modules, etc. are never rendered on
// GitHub, so they must not be validated). Fall back to a filesystem walk when
// not inside a git work tree.
function gitTrackedMarkdown(root) {
  const out = execFileSync('git', ['-C', root, 'ls-files', '-z', '--', '*.md', '*.markdown'], {
    encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
  });
  return out.split('\0').filter(Boolean).map((p) => path.join(root, p));
}

function walk(dir, acc) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      walk(path.join(dir, entry.name), acc);
    } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) {
      acc.push(path.join(dir, entry.name));
    }
  }
  return acc;
}

function collectMarkdown(root) {
  try {
    return gitTrackedMarkdown(root);
  } catch {
    console.warn('git ls-files unavailable — falling back to filesystem walk.');
    return walk(root, []);
  }
}

// ── Extract fenced ```mermaid blocks with their starting line numbers ───────
function extractBlocks(file) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  const blocks = [];
  let inBlock = false, buf = [], startLine = 0;
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].trim();
    if (!inBlock && t === '```mermaid') { inBlock = true; buf = []; startLine = i + 2; continue; }
    if (inBlock && t === '```') { blocks.push({ startLine, code: buf.join('\n') }); inBlock = false; continue; }
    if (inBlock) buf.push(lines[i]);
  }
  return blocks;
}

const files = collectMarkdown(ROOT).sort();
let total = 0, failed = 0;
const failures = [];

for (const file of files) {
  const rel = path.relative(ROOT, file).replace(/\\/g, '/');
  for (const { startLine, code } of extractBlocks(file)) {
    total++;
    const type = (code.trim().split(/\s+/)[0]) || '(empty)';
    try {
      await mermaid.parse(code);
    } catch (err) {
      failed++;
      const msg = String(err && err.message ? err.message : err).split('\n').slice(0, 6).join('\n      ');
      failures.push(`✗ ${rel}:${startLine}  [${type}]\n      ${msg}`);
    }
  }
}

if (failures.length) {
  console.error('\nMermaid validation FAILED:\n');
  for (const f of failures) console.error(f + '\n');
}
console.log(`Mermaid: ${total - failed}/${total} blocks parse cleanly across ${files.length} markdown files.`);
if (failed) {
  console.error(`\n${failed} diagram(s) would render as "Unable to render" on GitHub. Fix the syntax above.`);
  process.exit(1);
}
