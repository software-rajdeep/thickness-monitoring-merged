/**
 * Thickness Monitor — Electron Main Process (ESM)
 *
 * Using ESM (.mjs) to bypass the CJS npm 'electron' package shadow.
 * Electron 33 registers 'electron' as a native built-in module,
 * but the npm CJS package shadows it. ESM import has different
 * resolution rules and can access the real built-in module.
 *
 * NOTE: This file MUST be .mjs (not .js) to avoid the CJS shim.
 */

const path = (await import('path')).default;
const { fileURLToPath } = await import('url');
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─── Load Electron's built-in module ─────────────────────────────────────────
// We use dynamic import() which bypasses CJS require and can access
// Electron's native ESM built-in module.
let app, BrowserWindow, Menu, net, powerSaveBlocker;

try {
  const electron = await import('electron');
  app = electron.app;
  BrowserWindow = electron.BrowserWindow;
  Menu = electron.Menu;
  net = electron.net;
  powerSaveBlocker = electron.powerSaveBlocker;
  
  if (!app) {
    throw new Error('app not available from import');
  }
} catch (err) {
  // Fallback: try different import paths
  try {
    const electronFromPath = await import(
      path.join(__dirname, 'node_modules', 'electron', 'dist', 'resources', 'default_app.asar', 'main.js')
    );
    app = electronFromPath.app;
    BrowserWindow = electronFromPath.BrowserWindow;
    Menu = electronFromPath.Menu;
    net = electronFromPath.net;
    powerSaveBlocker = electronFromPath.powerSaveBlocker;
  } catch (err2) {
    console.error('[Electron] Fatal: Could not load Electron module:', err.message);
    console.error('[Electron] Fallback also failed:', err2.message);
    process.exit(1);
  }
}

// ─── Constants ───────────────────────────────────────────────────────────────
const TARGET_URL = 'https://merged-version.vercel.app';
const OFFLINE_FILE = path.join(__dirname, 'offline.html');
const ONLINE_PING_MS = 5000;
const OFFLINE_PING_MS = 3000;
const PING_URL = 'https://clients3.google.com/generate_204';
const PING_TIMEOUT = 4000;
const IS_DEV = process.env.NODE_ENV === 'development';

let mainWindow = null, pingTimer = null, isOnline = true;

function checkConnectivity() {
  return new Promise((resolve) => {
    const req = net.request({ method: 'HEAD', url: PING_URL });
    const t = setTimeout(() => { req.abort(); resolve(false); }, PING_TIMEOUT);
    req.on('response', () => { clearTimeout(t); resolve(true); });
    req.on('error', () => { clearTimeout(t); resolve(false); });
    req.on('abort', () => { clearTimeout(t); resolve(false); });
    req.end();
  });
}

async function onPing() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const online = await checkConnectivity();
  if (online === isOnline) return;
  isOnline = online;
  mainWindow.loadURL(online ? TARGET_URL : 'file://' + OFFLINE_FILE);
  startPing(online ? ONLINE_PING_MS : OFFLINE_PING_MS);
}

function startPing(ms) { if (pingTimer) clearInterval(pingTimer); pingTimer = setInterval(onPing, ms); }

function createWindow() {
  Menu.setApplicationMenu(null);
  mainWindow = new BrowserWindow({
    fullscreen: true, autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      devTools: IS_DEV,
    },
    show: false, backgroundColor: '#0f172a',
  });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  if (!IS_DEV) {
    mainWindow.webContents.on('before-input-event', (e, i) => {
      if (i.key === 'F12' || (i.control && i.shift && (i.key === 'I' || i.key === 'J'))) e.preventDefault();
    });
    mainWindow.webContents.on('context-menu', e => e.preventDefault());
  }
  mainWindow.loadURL(TARGET_URL);
  startPing(ONLINE_PING_MS);
  mainWindow.webContents.on('did-fail-load', (_, c, d) => console.warn(`[Electron] did-fail-load: ${c} — ${d}`));
  mainWindow.on('closed', () => { clearInterval(pingTimer); mainWindow = null; });
  mainWindow.webContents.on('will-navigate', (e, url) => { if (!url.startsWith(TARGET_URL)) e.preventDefault(); });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
}

let blockerId = null;
function preventSleep() { if (blockerId === null) blockerId = powerSaveBlocker.start('prevent-display-sleep'); }
function allowSleep() { if (blockerId !== null) { powerSaveBlocker.stop(blockerId); blockerId = null; } }

app.whenReady().then(() => { createWindow(); preventSleep(); });
app.on('window-all-closed', () => { allowSleep(); clearInterval(pingTimer); if (process.platform !== 'darwin') app.quit(); });
app.on('will-quit', () => { allowSleep(); clearInterval(pingTimer); console.log('[Electron] Quit. pi_client.py unaffected.'); });
process.on('uncaughtException', e => console.error('[Electron]', e));