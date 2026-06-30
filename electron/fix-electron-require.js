/**
 * fix-electron-require.js
 *
 * After 'npm install', this script removes the npm 'electron' package's
 * CJS entry point so require('electron') resolves to Electron's built-in
 * module when running inside Electron's process.
 *
 * The npm 'electron' package's index.js returns a binary path string.
 * This shadows Electron's built-in module. We remove the entry point so
 * Node.js falls through to the real built-in module.
 *
 * Additionally, we ensure the binary and CLI wrapper still work by
 * preserving the dist/ directory and cli.js.
 *
 * Run: node fix-electron-require.js
 */

const fs = require('fs');
const path = require('path');

const electronDir = path.join(__dirname, 'node_modules', 'electron');

if (!fs.existsSync(electronDir)) {
  console.error('[fix] ERROR: node_modules/electron not found. Run "npm install" first.');
  process.exit(1);
}

const indexPath = path.join(electronDir, 'index.js');
const pkgJsonPath = path.join(electronDir, 'package.json');
const bakDir = path.join(electronDir, '.bak');

// Backup index.js
if (fs.existsSync(indexPath) && !fs.existsSync(indexPath + '.bak')) {
  fs.copyFileSync(indexPath, indexPath + '.bak');
  console.log('[fix] Backed up index.js');
}

// Remove index.js (the CJS shim that returns binary path)
if (fs.existsSync(indexPath)) {
  fs.unlinkSync(indexPath);
  console.log('[fix] Removed index.js (npm CJS shim)');
}

// Remove "main" from package.json so Node.js doesn't find an entry point
if (fs.existsSync(pkgJsonPath)) {
  const pkg = JSON.parse(fs.readFileSync(pkgJsonPath, 'utf-8'));
  if (pkg.main) {
    delete pkg.main;
    fs.writeFileSync(pkgJsonPath, JSON.stringify(pkg, null, 2) + '\n');
    console.log('[fix] Removed "main" from package.json');
  }
}

console.log('[fix] Done. require("electron") will now resolve to the built-in module.');
console.log('[fix] Run with: ./node_modules/electron/dist/electron .');