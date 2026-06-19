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

export const SENSOR_CONFIGS = {
  A: { ip: "192.168.5.200", port: 8234, name: "Sensor A" },
  B: { ip: "192.168.5.201", port: 8234, name: "Sensor B" },
};

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