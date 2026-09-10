import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

export const digest = data => createHash('sha256').update(data).digest('hex');
export const json = value => `${JSON.stringify(value, null, 2)}\n`;
export const validVersion = value => typeof value === 'string' && /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value)
  && value.split('.').every(n => Number.isSafeInteger(Number(n)));

export function relativePath(value) {
  if (typeof value !== 'string' || !value || value.includes('\\') || value.includes('\0')
      || path.posix.isAbsolute(value) || value.split('/').some(p => !p || p === '.' || p === '..')) {
    throw new Error(`Unsafe relative path: ${value}`);
  }
  return value;
}

// Inspect every path segment, including missing destinations, before reading or writing.
export function safePath(root, relative) {
  let current = path.resolve(root);
  for (const part of relativePath(relative).split('/')) {
    current = path.join(current, part);
    try {
      if (fs.lstatSync(current).isSymbolicLink()) throw new Error(`Symlink not allowed: ${current}`);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  return current;
}

export function readOptional(file) {
  try { return fs.readFileSync(file); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return null;
  }
}

export function validateLock(lock) {
  if (lock.schemaVersion !== 1 || !validVersion(lock.release) || typeof lock.repo !== 'string'
      || !Array.isArray(lock.scopes) || !lock.files || Array.isArray(lock.files)
      || !Object.keys(lock.files).length) throw new Error('Invalid shared UI lock');
  relativePath(lock.repo);
  for (const scope of lock.scopes) relativePath(scope);
  for (const [file, entry] of Object.entries(lock.files)) {
    relativePath(file);
    if (!entry || typeof entry.source !== 'string' || !/^[a-f0-9]{64}$/.test(entry.sha256)) {
      throw new Error(`Invalid shared UI entry: ${file}`);
    }
  }
  return lock;
}

export function scopedFiles(root, scopes) {
  const found = [];
  const ignored = new Set(['node_modules', 'dist', '.git', '.DS_Store']);
  function walk(relative) {
    const absolute = safePath(root, relative);
    if (!fs.existsSync(absolute)) return;
    if (fs.statSync(absolute).isDirectory()) {
      for (const name of fs.readdirSync(absolute).sort()) {
        if (!ignored.has(name)) walk(`${relative}/${name}`);
      }
    } else found.push(relative);
  }
  for (const scope of scopes) walk(scope);
  return [...new Set(found)].sort();
}

export function checkTree(root, lock) {
  validateLock(lock);
  const issues = [];
  for (const [file, entry] of Object.entries(lock.files)) {
    try {
      const data = readOptional(safePath(root, file));
      if (data === null) issues.push(`${file}: missing (${entry.source})`);
      else if (digest(data) !== entry.sha256) issues.push(`${file}: drift (${entry.source}, expected ${entry.sha256.slice(0, 12)}, actual ${digest(data).slice(0, 12)})`);
    } catch (error) { issues.push(`${file}: ${error.message}`); }
  }
  try {
    for (const file of scopedFiles(root, lock.scopes)) {
      if (!Object.hasOwn(lock.files, file)) issues.push(`${file}: unregistered shared file`);
    }
    // Prevent accidentally adding a second, unmanaged shared UI directory.
    for (const scope of ['mine-troutfarm-ui', 'src/shared', 'src/portalUi', 'src/app/components/ui', 'src/components/ui']) {
      if (fs.existsSync(safePath(root, scope)) && !lock.scopes.includes(scope)) {
        issues.push(`${scope}: unregistered shared directory`);
      }
    }
  } catch (error) { issues.push(error.message); }
  return issues;
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const root = fileURLToPath(new URL('../', import.meta.url));
    const lock = JSON.parse(fs.readFileSync(safePath(root, '.shared-ui/lock.json'), 'utf8'));
    const issues = checkTree(root, lock);
    if (issues.length) throw new Error(issues.join('\n'));
    console.log(`Shared UI ${lock.release}: ${lock.repo}, ${Object.keys(lock.files).length} files OK`);
  } catch (error) {
    console.error(`Shared UI check failed:\n${error.message}\nDo not regenerate hashes locally. Review the portal tools/shared-ui catalog and release procedure.`);
    process.exitCode = 1;
  }
}
