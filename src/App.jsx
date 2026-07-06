import { useState, useRef, useEffect } from "react";
import "./styles/global.css";
import { io } from "socket.io-client";

import Topbar  from "./layout/Topbar";
import Sidebar from "./layout/Sidebar";
import Toast   from "./components/Toast";
import ModeSelection from "./components/ModeSelection";

// Side-by-side mode pages (original)
import SbsLoginPage        from "./pages/LoginPage";
import SbsDashboardPage    from "./pages/DashboardPage";
import SbsSensorConfigPage from "./pages/SensorConfigPage";
import SbsRunModePage      from "./pages/RunModePage";
import SbsDownloadPage     from "./pages/DownloadPage";
import SbsBackendPage      from "./pages/BackendPage";

// Opposite-side mode pages
import OppLoginPage        from "./pages/opposite/LoginPage";
import OppDashboardPage    from "./pages/opposite/DashboardPage";
import OppSensorConfigPage from "./pages/opposite/SensorConfigPage";
import OppRunModePage      from "./pages/opposite/RunModePage";
import OppDownloadPage     from "./pages/opposite/DownloadPage";
import OppBackendPage      from "./pages/opposite/BackendPage";

// Opposite mode layout
import OppTopbar  from "./layout/opposite/Topbar";
import OppSidebar from "./layout/opposite/Sidebar";

import { SERVER, fetchSensorConfigs as fetchSbsConfigs } from "./constants/config";
import { fetchSensorConfigs as fetchOppConfigs } from "./constants/config_opposite";
import { fetchDevices, getDeviceId, setDeviceId, setToken, authHeaders } from "./constants/auth";

export default function App() {
  const [sensorMode,  setSensorMode]  = useState(null); // null | "side-by-side" | "opposite"
  const [user,        setUser]        = useState(null);
  const [page,        setPage]        = useState("dashboard");
  const [toast,       setToast]       = useState(null);
  const [live,        setLive]        = useState(false);
  const [connected,   setConnected]   = useState(false);
  const [rows,        setRows]        = useState([]);
  const [streamRate,  setStreamRate]  = useState(null);
  // sensor-level connectivity (distinct from `connected`, which is the socket).
  // false ⇒ sensors are offline / no fresh data is arriving from the sensors.
  const [sensorsOnline, setSensorsOnline] = useState(true);
  const [thicknessState, setThicknessState] = useState(null);
  const [calibrationBusy, setCalibrationBusy] = useState(false);
  const [runModeVisitKey, setRunModeVisitKey] = useState(0);

  // Thickness limit state persists across page navigations.
  // It is also GLOBAL: stored on the server so it is shared across all users
  // and sessions — set it once and every later login sees the same limit.
  const [thicknessLimit, setThicknessLimit] = useState({ active: false, min: "", max: "" });
  // Mirror of thicknessLimit kept in a ref so updateThicknessLimit() can resolve
  // functional updaters and persist the result without a stale closure.
  const thicknessLimitRef = useRef(thicknessLimit);

  // Multi-tenant: the device whose live stream / data this session is viewing.
  const [devices, setDevices]               = useState([]);
  const [selectedDevice, setSelectedDevice] = useState(null);
  const selectedDeviceRef                   = useRef(null);

  const socketRef       = useRef(null);
  const counterRef      = useRef(1);
  const dataBufferRef   = useRef([]);
  const lastReadingTime = useRef(null);

  // ── Mode selection ─────────────────────────────────────────────────────
  function handleSelectMode(mode) {
    setSensorMode(mode);
    // Fetch sensor configs from server (dynamic, not hardcoded)
    const fetcher = mode === "opposite" ? fetchOppConfigs : fetchSbsConfigs;
    fetcher(mode === "opposite" ? "opposite" : "sbs").then(configs => {
      if (Object.keys(configs).length === 0) {
        console.warn("No sensor configs received from server.");
        console.warn("Ensure sensor_network.json exists on the server.");
      }
    });
  }

  // ── Shared helpers ──────────────────────────────────────────────────────
  function showToast(msg, type = "success") {
    setToast({ msg, type, key: Date.now() });
  }

  // Append the selected device to scope a thickness call to that device.
  function deviceQuery() {
    const d = selectedDeviceRef.current;
    return d ? `?device_id=${encodeURIComponent(d)}` : "";
  }

  async function loadThicknessState() {
    try {
      const response = await fetch(`${SERVER}/thickness/state${deviceQuery()}`, { headers: authHeaders() });
      if (!response.ok) return;
      const data = await response.json();
      setThicknessState(data);
    } catch {
      // Keep the UI usable even if the thickness-state endpoint is unavailable.
    }
  }

  async function refreshThicknessState() {
    try {
      const response = await fetch(`${SERVER}/thickness/state${deviceQuery()}`, { headers: authHeaders() });
      if (!response.ok) return null;
      const data = await response.json();
      setThicknessState(data);
      return data;
    } catch {
      return null;
    }
  }

  // Load the GLOBAL thickness limit from the server (shared across all users).
  async function loadThicknessLimit() {
    try {
      const response = await fetch(`${SERVER}/thickness/limit`, { headers: authHeaders() });
      if (!response.ok) return;
      const data = await response.json();
      const next = {
        active: !!data.active,
        min: data.min ?? "",
        max: data.max ?? "",
      };
      thicknessLimitRef.current = next;
      setThicknessLimit(next);
    } catch {
      // Keep the UI usable even if the limit endpoint is unavailable.
    }
  }

  // Update the thickness limit locally AND persist it to the server so it
  // stays global. Accepts either a new value or a functional updater, matching
  // the React setState signature the page components already use.
  function updateThicknessLimit(updater) {
    const next = typeof updater === "function"
      ? updater(thicknessLimitRef.current)
      : updater;
    thicknessLimitRef.current = next;
    setThicknessLimit(next);
    // Fire-and-forget: the local state is the source of truth for the UI; the
    // server copy just makes it survive logout / login-as-another-user.
    fetch(`${SERVER}/thickness/limit`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(next),
    }).catch(() => {});
  }

  // ── Side-by-side mode handlers ─────────────────────────────────────────
  async function handleApplyCalibration(referenceThickness) {
    setCalibrationBusy(true);
    try {
      const response = await fetch(`${SERVER}/thickness/calibration`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          reference_thickness: referenceThickness,
          username: user?.username,
          device_id: selectedDeviceRef.current,
        }),
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to save calibration", "error");
        return false;
      }

      await refreshThicknessState();
      const warningText = data.warnings?.length ? ` ${data.warnings.join(" ")}` : "";
      showToast(`${data.message}${warningText}`, data.warnings?.length ? "error" : "success");
      return true;
    } catch {
      showToast("Unable to save calibration", "error");
      return false;
    } finally {
      setCalibrationBusy(false);
    }
  }

  async function handleResetCalibration() {
    setCalibrationBusy(true);
    try {
      const response = await fetch(`${SERVER}/thickness/calibration/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ username: user?.username, device_id: selectedDeviceRef.current }),
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to reset calibration", "error");
        return false;
      }

      await refreshThicknessState();
      showToast(data.message || "Calibration reset successfully", "success");
      return true;
    } catch {
      showToast("Unable to reset calibration", "error");
      return false;
    } finally {
      setCalibrationBusy(false);
    }
  }

  // ── Opposite-side mode handlers ────────────────────────────────────────
  async function handleSetAutoGap(objectThickness, toleranceMin, toleranceMax) {
    setCalibrationBusy(true);
    try {
      const response = await fetch(`${SERVER}/thickness/auto-gap`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          object_thickness: objectThickness,
          thickness_tolerance_min: toleranceMin || null,
          thickness_tolerance_max: toleranceMax || null,
          username: user?.username,
          device_id: selectedDeviceRef.current,
        }),
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to set auto gap", "error");
        return false;
      }

      await refreshThicknessState();
      showToast(data.message || "Auto-gap setup successfully", "success");
      return true;
    } catch {
      showToast("Unable to set auto gap", "error");
      return false;
    } finally {
      setCalibrationBusy(false);
    }
  }

  async function handleSetGapDistance(gapDistance) {
    setCalibrationBusy(true);
    try {
      const response = await fetch(`${SERVER}/thickness/gap`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          gap_distance: gapDistance,
          username: user?.username,
          device_id: selectedDeviceRef.current,
        }),
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to set gap distance", "error");
        return false;
      }

      await refreshThicknessState();
      showToast(data.message || "Gap distance set successfully", "success");
      return true;
    } catch {
      showToast("Unable to set gap distance", "error");
      return false;
    } finally {
      setCalibrationBusy(false);
    }
  }

  async function handleResetGap() {
    setCalibrationBusy(true);
    try {
      const response = await fetch(`${SERVER}/thickness/calibration/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ username: user?.username, device_id: selectedDeviceRef.current }),
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to reset", "error");
        return false;
      }

      await refreshThicknessState();
      showToast(data.message || "Reset successfully", "success");
      return true;
    } catch {
      showToast("Unable to reset", "error");
      return false;
    } finally {
      setCalibrationBusy(false);
    }
  }

  // ── Socket ──────────────────────────────────────────────────────────────
  function connectSocket() {
    if (socketRef.current) return;
    const socket = io(SERVER, { transports: ["polling", "websocket"] });

    socket.on("connect", () => {
      setConnected(true);
      setLive(true);
      // Subscribe only to the selected device's room (tenant isolation).
      const did = selectedDeviceRef.current;
      if (did) socket.emit("join_device", { device_id: did });
    });

    socket.on("disconnect", () => {
      setConnected(false);
      setLive(false);
      setSensorsOnline(true); // reset; banner is gated on `live` anyway
    });

    // Server-pushed sensor online/offline status (authoritative).
    socket.on("sensor_status", (data) => {
      setSensorsOnline(!!(data && data.online));
    });

    socket.on("sensor_reading", (data) => {
      let row;
      if (sensorMode === "opposite") {
        row = {
          id: counterRef.current++,
          ts: data.timestamp
            ? data.timestamp.replace("T", " ").slice(0, 23)
            : new Date().toISOString().replace("T", " ").slice(0, 23),
          a: data.distance_A ?? null,
          b: data.distance_B ?? null,
          thickness: data.thickness ?? null,
        };
      } else {
        row = {
          id: counterRef.current++,
          ts: data.timestamp
            ? data.timestamp.replace("T", " ").slice(0, 23)
            : new Date().toISOString().replace("T", " ").slice(0, 23),
          a: data.distance_A ?? null,
          b: data.distance_B ?? null,
          c: data.distance_C ?? null,
        };
      }

      dataBufferRef.current = [row, ...dataBufferRef.current].slice(0, 100000);
      setRows(prev => [row, ...prev.slice(0, 99)]);

      // Stream rate detect
      const now = Date.now();
      if (lastReadingTime.current) {
        const diff = now - lastReadingTime.current;
        const hz   = Math.round(1000 / diff);
        if (hz >= 1 && hz <= 20) setStreamRate(String(hz));
      }
      lastReadingTime.current = now;
      // A reading means the sensors are live.
      setSensorsOnline(true);
    });

    socketRef.current = socket;
  }

  function disconnectSocket() {
    if (socketRef.current) {
      socketRef.current.disconnect();
      socketRef.current = null;
    }
    setConnected(false);
    setLive(false);
  }

  function handleToggle() {
    if (live) disconnectSocket();
    else connectSocket();
  }

  // ── Login / Logout ───────────────────────────────────────────────────────
  async function handleLogin(u) {
    setUser(u);
    setPage("dashboard");

    // Load this user's devices and pick one to view. Prefer a previously chosen
    // device if it is still visible, else the legacy device, else the first.
    const list = await fetchDevices();
    setDevices(list);
    let did = null;
    if (list.length) {
      const prev = getDeviceId();
      const ids = list.map(d => d.device_id);
      did = (prev && ids.includes(prev)) ? prev
          : (ids.includes("dev_legacy") ? "dev_legacy" : list[0].device_id);
    }
    selectedDeviceRef.current = did;
    setSelectedDevice(did);
    setDeviceId(did);

    // Calibration state and thickness limit are now global (shared across all users)
    await loadThicknessState();
    await loadThicknessLimit();
  }

  // Switch which device's live stream this session views. Reconnect the socket so
  // it leaves the old room and joins the new one; clear the stale chart buffer.
  function changeDevice(did) {
    if (!did || did === selectedDeviceRef.current) return;
    selectedDeviceRef.current = did;
    setSelectedDevice(did);
    setDeviceId(did);
    setRows([]);
    dataBufferRef.current = [];
    counterRef.current = 1;
    loadThicknessState();   // reflect the new device's calibration
    if (socketRef.current) {
      disconnectSocket();
      connectSocket();
    }
  }

  async function handleLogout() {
    // Do NOT reset calibration on logout — we save per-user calibration
    // so it persists across sessions. Calibration state stays in the runtime.

    disconnectSocket();
    setToken(null);
    setDeviceId(null);
    setDevices([]);
    setSelectedDevice(null);
    selectedDeviceRef.current = null;
    setUser(null);
    setPage("dashboard");
    setRows([]);
    setStreamRate(null);
    setThicknessState(null);
    setCalibrationBusy(false);
    setRunModeVisitKey(0);
    setThicknessLimit({ active: false, min: "", max: "" });
    try { window.localStorage.removeItem("thicknessmon.calibrated"); } catch {}
    dataBufferRef.current   = [];
    counterRef.current      = 1;
    lastReadingTime.current = null;
  }

  // ── Reset mode (full logout including mode) ──────────────────────────────
  function handleBackToModeSelection() {
    handleLogout();
    setSensorMode(null);
  }

  useEffect(() => {
    if (!user) return;
    loadThicknessState();
    loadThicknessLimit();
  }, [user]);

  // Keep the ref in sync with state for any external setThicknessLimit callers
  // (e.g. the logout reset) so updateThicknessLimit never resolves a stale value.
  useEffect(() => {
    thicknessLimitRef.current = thicknessLimit;
  }, [thicknessLimit]);

  useEffect(() => {
    if (page === "run-mode") {
      setRunModeVisitKey(key => key + 1);
    }
  }, [page]);

  // ── Auto-start live reading when visiting run-mode ──
  useEffect(() => {
    if (page === "run-mode" && !socketRef.current) {
      connectSocket();
    }
  }, [page]);

  useEffect(() => {
    return () => {
      if (socketRef.current) socketRef.current.disconnect();
    };
  }, []);

  // Sensor-offline watchdog (backup for the server's sensor_status event):
  // while live, if no reading has arrived for >3.5 s, treat sensors as offline.
  useEffect(() => {
    if (!live) { setSensorsOnline(true); return; }
    const iv = setInterval(() => {
      if (lastReadingTime.current && Date.now() - lastReadingTime.current > 3500) {
        setSensorsOnline(false);
      }
    }, 1000);
    return () => clearInterval(iv);
  }, [live]);

  // ── Show mode selection first if no mode chosen ──
  if (!sensorMode) return <ModeSelection onSelectMode={handleSelectMode} />;

  // ── Mode is chosen but no user → show login ──
  const isOpposite = sensorMode === "opposite";
  const LoginComponent = isOpposite ? OppLoginPage : SbsLoginPage;

  if (!user) return <LoginComponent onLogin={handleLogin} />;

  // ── Logged in ──
  const ActiveTopbar  = isOpposite ? OppTopbar : Topbar;
  const ActiveSidebar = isOpposite ? OppSidebar : Sidebar;
  const DashboardPage    = isOpposite ? OppDashboardPage : SbsDashboardPage;
  const SensorConfigPage = isOpposite ? OppSensorConfigPage : SbsSensorConfigPage;
  const DownloadPage     = isOpposite ? OppDownloadPage : SbsDownloadPage;
  const BackendPage      = isOpposite ? OppBackendPage : SbsBackendPage;

  return (
    <div className="app-shell">
      <ActiveTopbar user={user} page={page} onLogout={handleBackToModeSelection} />

      {selectedDevice && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, padding: "6px 18px",
          background: "#0f172a", color: "#cbd5e1", fontSize: 13,
          borderBottom: "1px solid #1e293b",
        }}>
          <span style={{ opacity: 0.7 }}>Device:</span>
          {devices.length > 1 ? (
            <select
              value={selectedDevice}
              onChange={e => changeDevice(e.target.value)}
              style={{
                background: "#1e293b", color: "#fff", border: "1px solid #334155",
                borderRadius: 6, padding: "3px 8px",
              }}
            >
              {devices.map(d => (
                <option key={d.device_id} value={d.device_id}>
                  {(d.customer_name ? d.customer_name + " — " : "") + (d.label || d.device_id)}
                </option>
              ))}
            </select>
          ) : (
            <strong style={{ color: "#fff" }}>
              {(() => {
                const d = devices.find(x => x.device_id === selectedDevice);
                return d
                  ? (d.customer_name ? d.customer_name + " — " : "") + (d.label || d.device_id)
                  : selectedDevice;
              })()}
            </strong>
          )}
        </div>
      )}

      {live && !sensorsOnline && (
        <div style={{
          background: "#c62828", color: "#fff", padding: "10px 18px",
          fontSize: 14, fontWeight: 600, textAlign: "center",
          display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
        }}>
          ⚠ Sensors disconnected — no live data is being received from the sensors.
        </div>
      )}

      <div className="content-area">
        <ActiveSidebar user={user} page={page} onNavigate={setPage} onLogout={handleBackToModeSelection} />

        <div className="main">
          {page === "dashboard" && (
            <DashboardPage
              user={user}
              onNavigate={setPage}
              rows={rows}
              streamRate={streamRate}
              sensorsOnline={sensorsOnline}
            />
          )}
          {page === "sensor-config" && (
            <SensorConfigPage user={user} onToast={showToast} />
          )}
          {page === "run-mode" && (
            isOpposite ? (
              <OppRunModePage
                user={user}
                rows={rows}
                live={live}
                connected={connected}
                sensorsOnline={sensorsOnline}
                onToggle={handleToggle}
                thicknessState={thicknessState}
                onSetGapDistance={handleSetGapDistance}
                onSetAutoGap={handleSetAutoGap}
                onResetGap={handleResetGap}
                calibrationBusy={calibrationBusy}
                runModeVisitKey={runModeVisitKey}
                thicknessLimit={thicknessLimit}
                setThicknessLimit={updateThicknessLimit}
              />
            ) : (
              <SbsRunModePage
                user={user}
                rows={rows}
                live={live}
                connected={connected}
                sensorsOnline={sensorsOnline}
                onToggle={handleToggle}
                thicknessState={thicknessState}
                onApplyCalibration={handleApplyCalibration}
                onResetCalibration={handleResetCalibration}
                calibrationBusy={calibrationBusy}
                runModeVisitKey={runModeVisitKey}
                thicknessLimit={thicknessLimit}
                setThicknessLimit={updateThicknessLimit}
              />
            )
          )}
          {page === "download" && (
            <DownloadPage
              user={user}
              onToast={showToast}
              dataBufferRef={dataBufferRef}
            />
          )}
          {page === "backend" && (
            <BackendPage user={user} />
          )}
        </div>
      </div>

      {toast && (
        <Toast key={toast.key} msg={toast.msg} type={toast.type} onDone={() => setToast(null)} />
      )}
    </div>
  );
}