// Multi-tenant auth + device selection helpers.
// Token-based login (/auth/login) and the per-customer device list (/auth/devices).
import { SERVER } from "./config";

const TOKEN_KEY  = "thicknessmon.token";
const DEVICE_KEY = "thicknessmon.device";

export function getToken() {
  try { return window.localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(t) {
  try { t ? window.localStorage.setItem(TOKEN_KEY, t) : window.localStorage.removeItem(TOKEN_KEY); } catch {}
}
export function getDeviceId() {
  try { return window.localStorage.getItem(DEVICE_KEY); } catch { return null; }
}
export function setDeviceId(d) {
  try { d ? window.localStorage.setItem(DEVICE_KEY, d) : window.localStorage.removeItem(DEVICE_KEY); } catch {}
}
export function authHeaders() {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

// Log in with company + username + password. Stores the token.
// Leave `company` blank for a global (Rajdeep) account such as the global superadmin.
export async function login(company, username, password) {
  const res = await fetch(`${SERVER}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ company, username, password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Invalid credentials");
  setToken(data.token);
  return data.user; // { id, username, email, role, customer_id, customer_name }
}

// Devices visible to the logged-in user (superadmin → all, others → own customer).
export async function fetchDevices() {
  try {
    const res = await fetch(`${SERVER}/auth/devices`, { headers: authHeaders() });
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}
