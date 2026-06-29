/**
 * Thickness Monitor — Electron Preload Script
 *
 * Exposes minimal IPC bridges to the renderer process via contextBridge.
 * The renderer (Vercel website) has no direct Node.js access — this is
 * strictly for offline/online status notifications.
 *
 * Security: contextIsolation=true, nodeIntegration=false, sandbox=true
 */

const { contextBridge } = require('electron');

/**
 * Minimal API exposed as `window.electronAPI` in the renderer.
 * Currently provides connectivity status events for the offline page.
 */
contextBridge.exposeInMainWorld('electronAPI', {
  /**
   * Returns basic platform info (could be useful for debugging).
   */
  getPlatform: () => process.platform,

  /**
   * Returns the app version from package.json.
   */
  getVersion: () => '1.0.0',
});