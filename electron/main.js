/**
 * Thickness Monitor — Electron Main Process
 *
 * Launches a full-screen BrowserWindow pointed at the Vercel-deployed frontend.
 * Detects network outages with a periodic ping and shows a professional
 * offline page. Automatically reloads the app when connectivity returns.
 *
 * This process does NOT manage or interact with pi_client.py in any way.
 * pi_client.py runs independently via the pi-merged-client systemd service.
 */

const { app, BrowserWindow, Menu, dialog, powerSaveBlocker } = require('electron');
const path = require('path');
const { net } = require('electron');

// ─── Constants ───────────────────────────────────────────────────────────────

const TARGET_URL = 'https://merged-version.vercel.app';
const OFFLINE_FILE = path.join(__dirname, 'offline.html');

/** Ping interval while online (ms) — how often we confirm connectivity */
const ONLINE_PING_INTERVAL = 5000;

/** Ping interval while offline (ms) — how often we retry */
const OFFLINE_RETRY_INTERVAL = 3000;

/** URL used for connectivity checks (Google's 204 generator — lightweight, no payload) */
const PING_URL = 'https://clients3.google.com/generate_204';

/** Maximum time (ms) to wait for a ping response before declaring offline */
const PING_TIMEOUT = 4000;

/** Whether we are in development mode */
const IS_DEV = process.env.NODE_ENV === 'development';

// ─── State ───────────────────────────────────────────────────────────────────

let mainWindow = null;
let pingTimer = null;
let isAppOnline = true;       // tracks our last-known connectivity state
let isShowingOffline = false; // whether offline.html is currently displayed

// ─── Connectivity Detection ──────────────────────────────────────────────────

/**
 * Perform a lightweight connectivity check.
 * Returns true if the ping URL responds successfully within the timeout.
 */
function checkConnectivity() {
  return new Promise((resolve) => {
    const request = net.request({
      method: 'HEAD',
      url: PING_URL,
    });

    const timer = setTimeout(() => {
      request.abort();
      resolve(false);
    }, PING_TIMEOUT);

    request.on('response', (response) => {
      clearTimeout(timer);
      // Any HTTP response (even 204, 404, etc.) means we have connectivity
      resolve(true);
    });

    request.on('error', () => {
      clearTimeout(timer);
      resolve(false);
    });

    request.on('abort', () => {
      clearTimeout(timer);
      resolve(false);
    });

    request.end();
  });
}

/**
 * Called periodically by the ping timer.
 * When connectivity state changes, we switch between the Vercel app and offline.html.
 */
async function handleConnectivityCheck() {
  if (!mainWindow || mainWindow.isDestroyed()) return;

  const online = await checkConnectivity();

  if (online === isAppOnline) {
    // No state change — nothing to do
    return;
  }

  // ── State changed ──────────────────────────────────────────────────────────
  isAppOnline = online;

  if (online) {
    // ── We just came back online → reload the real app ───────────────────────
    console.log('[Electron] Connectivity restored — reloading Vercel app.');
    isShowingOffline = false;
    mainWindow.loadURL(TARGET_URL);
    startPingLoop(ONLINE_PING_INTERVAL);
  } else {
    // ── We just went offline → show the offline page ─────────────────────────
    console.log('[Electron] Connectivity lost — showing offline page.');
    isShowingOffline = true;
    mainWindow.loadFile(OFFLINE_FILE);
    startPingLoop(OFFLINE_RETRY_INTERVAL);
  }
}

/**
 * Start or restart the periodic connectivity check loop.
 */
function startPingLoop(intervalMs) {
  if (pingTimer) {
    clearInterval(pingTimer);
    pingTimer = null;
  }
  pingTimer = setInterval(handleConnectivityCheck, intervalMs);
}

/**
 * Stop the periodic connectivity check loop.
 */
function stopPingLoop() {
  if (pingTimer) {
    clearInterval(pingTimer);
    pingTimer = null;
  }
}

// ─── App Lifecycle ───────────────────────────────────────────────────────────

function createWindow() {
  // Remove the default menu bar entirely (requirement: hide menu bar)
  Menu.setApplicationMenu(null);

  mainWindow = new BrowserWindow({
    fullscreen: true,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,        // security best practice
      nodeIntegration: false,        // security best practice
      sandbox: true,                 // sandbox renderer for production safety
      devTools: IS_DEV,              // only enable DevTools in dev mode
    },
    show: false, // show once 'ready-to-show' fires for a clean launch
    icon: undefined,
    backgroundColor: '#0f172a', // dark navy — matches offline.html background
    kiosk: false,               // NOT kiosk — allows graceful exit via Alt+F4
  });

  // Show window once content is ready (avoids white flash)
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // Disable Developer Tools in production
  if (!IS_DEV) {
    mainWindow.webContents.on('before-input-event', (event, input) => {
      // Block Ctrl+Shift+I, F12, Ctrl+Shift+J
      if (
        (input.key === 'F12') ||
        (input.control && input.shift && (input.key === 'I' || input.key === 'J'))
      ) {
        event.preventDefault();
      }
    });

    // Also prevent right-click → Inspect context menu
    mainWindow.webContents.on('context-menu', (event) => {
      event.preventDefault();
    });
  }

  // Load the target app
  mainWindow.loadURL(TARGET_URL);

  // Start connectivity monitoring
  isAppOnline = true;
  isShowingOffline = false;
  startPingLoop(ONLINE_PING_INTERVAL);

  // --- Event handlers ---

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription) => {
    // Some failures (e.g. DNS lookup failed) indicate network issues.
    // We let the ping loop handle recovery, but log the event.
    console.warn(`[Electron] did-fail-load: ${errorCode} — ${errorDescription}`);
  });

  mainWindow.on('closed', () => {
    stopPingLoop();
    mainWindow = null;
  });

  // Prevent navigation away from our target URL or offline.html
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const allowed = [TARGET_URL, OFFLINE_FILE].some((allowedUrl) => {
      return url.startsWith(allowedUrl);
    });
    if (!allowed) {
      event.preventDefault();
    }
  });

  // Prevent external links from opening in the Electron window
  mainWindow.webContents.setWindowOpenHandler(() => {
    return { action: 'deny' };
  });
}

// ─── Prevent Sleep (optional — prevents screen blanking on Pi) ──────────────

let blockerId = null;

function preventSleep() {
  if (blockerId === null) {
    blockerId = powerSaveBlocker.start('prevent-display-sleep');
    console.log(`[Electron] Power save blocker started (id=${blockerId})`);
  }
}

function allowSleep() {
  if (blockerId !== null) {
    powerSaveBlocker.stop(blockerId);
    console.log(`[Electron] Power save blocker stopped (id=${blockerId})`);
    blockerId = null;
  }
}

// ─── Main Process Events ─────────────────────────────────────────────────────

app.whenReady().then(() => {
  createWindow();
  preventSleep();

  // macOS: re-create window when dock icon is clicked (good cross-platform practice)
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  allowSleep();
  stopPingLoop();
  // Do NOT quit on macOS (convention), quit on other platforms
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('will-quit', () => {
  // Clean up resources.
  // Note: we do NOT touch pi_client.py here — it runs independently via systemd.
  allowSleep();
  stopPingLoop();
  console.log('[Electron] Quitting cleanly. pi_client.py remains unaffected.');
});

// Handle unexpected crashes gracefully
process.on('uncaughtException', (error) => {
  console.error('[Electron] Uncaught exception:', error);
  // Don't exit — let the app try to recover
});

process.on('unhandledRejection', (reason) => {
  console.warn('[Electron] Unhandled rejection:', reason);
});