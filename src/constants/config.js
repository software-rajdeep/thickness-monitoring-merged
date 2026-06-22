export const DEFAULT_SERVER = 
  import.meta.env.VITE_SERVER_URL || window.location.origin;
const STORAGE_KEY = "thicknessmon.server";

export function getServerBase() {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setServerBase(value) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // Ignore storage errors to keep the UI responsive.
  }
}

export const SERVER = getServerBase() || DEFAULT_SERVER;

export const DEMO_ACCOUNTS = [
  { username: "superadmin", password: "superadmin123", role: "superadmin" },
  { username: "admin",      password: "admin123",      role: "admin"      },
  { username: "supervisor", password: "super123",      role: "supervisor" },
  { username: "worker",     password: "worker123",     role: "worker"     },
];

// SENSOR_CONFIGS is populated dynamically from the server API at startup.
// The server reads sensor_network.json as the single source of truth.
// To change IP addresses, edit sensor_network.json — no frontend code changes needed.
export const SENSOR_CONFIGS = {};

/**
 * Fetch sensor configurations from the server.
 * Call this once on app startup to populate SENSOR_CONFIGS.
 * @param {string} mode - "sbs" (side-by-side) or "opposite"
 * @returns {Promise<object>} The sensor configs from the server
 */
export async function fetchSensorConfigs(mode = "sbs") {
  try {
    const base = SERVER;
    const response = await fetch(`${base}/server/config?mode=${mode}`);
    if (!response.ok) {
      throw new Error(`Server returned ${response.status}`);
    }
    const data = await response.json();
    const configs = data.sensor_configs || {};
    // Update the exported SENSOR_CONFIGS object in-place
    Object.keys(configs).forEach(key => {
      SENSOR_CONFIGS[key] = configs[key];
    });
    // Remove old keys that no longer exist
    Object.keys(SENSOR_CONFIGS).forEach(key => {
      if (!configs[key]) {
        delete SENSOR_CONFIGS[key];
      }
    });
    return configs;
  } catch (error) {
    console.warn("Failed to fetch sensor configs from server:", error.message);
    console.warn("Edit sensor_network.json on the server to configure sensors.");
    return {};
  }
}

export const NAV_ITEMS = [
  { id: "dashboard",      label: "Dashboard",     icon: "Dashboard", section: "main"  },
  { id: "sensor-config",  label: "Sensor Config", icon: "Sensor",    section: "main"  },
  { id: "run-mode",       label: "Run Mode",      icon: "Activity",  section: "main"  },
  { id: "download",       label: "Download Data", icon: "Download",  section: "main"  },
  { id: "backend",        label: "Backend",       icon: "Backend",   section: "admin" },
];

export const PAGE_LABELS = {
  "dashboard":     "Dashboard",
  "sensor-config": "Sensor Config",
  "run-mode":      "Run Mode",
  "download":      "Download Data",
  "backend":       "Backend",
};