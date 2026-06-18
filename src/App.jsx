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

import { SERVER } from "./constants/config";

export default function App() {
  const [sensorMode,  setSensorMode]  = useState(null); // null | "side-by-side" | "opposite"
  const [user,        setUser]        = useState(null);
  const [page,        setPage]        = useState("dashboard");
  const [toast,       setToast]       = useState(null);
  const [live,        setLive]        = useState(false);
  const [connected,   setConnected]   = useState(false);
  const [rows,        setRows]        = useState([]);
  const [streamRate,  setStreamRate]  = useState(null);
  const [thicknessState, setThicknessState] = useState(null);
  const [calibrationBusy, setCalibrationBusy] = useState(false);
  const [runModeVisitKey, setRunModeVisitKey] = useState(0);

  const socketRef       = useRef(null);
  const counterRef      = useRef(1);
  const dataBufferRef   = useRef([]);
  const lastReadingTime = useRef(null);

  // ── Mode selection ─────────────────────────────────────────────────────
  function handleSelectMode(mode) {
    setSensorMode(mode);
  }

  // ── Shared helpers ──────────────────────────────────────────────────────
  function showToast(msg, type = "success") {
    setToast({ msg, type, key: Date.now() });
  }

  async function loadThicknessState() {
    try {
      const response = await fetch(`${SERVER}/thickness/state`);
      if (!response.ok) return;
      const data = await response.json();
      setThicknessState(data);
    } catch {
      // Keep the UI usable even if the thickness-state endpoint is unavailable.
    }
  }

  async function refreshThicknessState() {
    try {
      const response = await fetch(`${SERVER}/thickness/state`);
      if (!response.ok) return null;
      const data = await response.json();
      setThicknessState(data);
      return data;
    } catch {
      return null;
    }
  }

  // ── Side-by-side mode handlers ─────────────────────────────────────────
  async function handleApplyCalibration(referenceThickness) {
    setCalibrationBusy(true);
    try {
      const response = await fetch(`${SERVER}/thickness/calibration`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reference_thickness: referenceThickness }),
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to save calibration", "error");
        return false;
      }

      await refreshThicknessState();
      try { window.localStorage.removeItem("thicknessmon.calibrated"); } catch {}
      try { window.localStorage.setItem("thicknessmon.calibrated", "1"); } catch {}
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
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to reset calibration", "error");
        return false;
      }

      await refreshThicknessState();
      try { window.localStorage.removeItem("thicknessmon.calibrated"); } catch {}
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          object_thickness: objectThickness,
          thickness_tolerance_min: toleranceMin || null,
          thickness_tolerance_max: toleranceMax || null,
        }),
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to set auto gap", "error");
        return false;
      }

      await refreshThicknessState();
      try { window.localStorage.removeItem("thicknessmon.calibrated"); } catch {}
      try { window.localStorage.setItem("thicknessmon.calibrated", "1"); } catch {}
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gap_distance: gapDistance }),
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to set gap distance", "error");
        return false;
      }

      await refreshThicknessState();
      try { window.localStorage.removeItem("thicknessmon.calibrated"); } catch {}
      try { window.localStorage.setItem("thicknessmon.calibrated", "1"); } catch {}
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
      });
      const data = await response.json();

      if (!response.ok) {
        showToast(data?.error || "Unable to reset", "error");
        return false;
      }

      await refreshThicknessState();
      try { window.localStorage.removeItem("thicknessmon.calibrated"); } catch {}
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
    const socket = io(SERVER, { transports: ["websocket"] });

    socket.on("connect", () => {
      setConnected(true);
      setLive(true);
    });

    socket.on("disconnect", () => {
      setConnected(false);
      setLive(false);
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
          a: data.sensor_A ?? null,
          b: data.sensor_B ?? null,
          c: data.sensor_C ?? null,
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
  function handleLogin(u) {
    setUser(u);
    setPage("dashboard");
  }

  async function handleLogout() {
    try {
      await fetch(`${SERVER}/thickness/calibration/reset`, {
        method: "POST",
      });
    } catch {
      // Logging out should still clear the local session even if the server is unavailable.
    }

    disconnectSocket();
    setUser(null);
    setPage("dashboard");
    setRows([]);
    setStreamRate(null);
    setThicknessState(null);
    setCalibrationBusy(false);
    setRunModeVisitKey(0);
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
  }, [user]);

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

      <div className="content-area">
        <ActiveSidebar user={user} page={page} onNavigate={setPage} onLogout={handleBackToModeSelection} />

        <div className="main">
          {page === "dashboard" && (
            <DashboardPage
              user={user}
              onNavigate={setPage}
              rows={rows}
              streamRate={streamRate}
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
                onToggle={handleToggle}
                thicknessState={thicknessState}
                onSetGapDistance={handleSetGapDistance}
                onSetAutoGap={handleSetAutoGap}
                onResetGap={handleResetGap}
                calibrationBusy={calibrationBusy}
                runModeVisitKey={runModeVisitKey}
              />
            ) : (
              <SbsRunModePage
                user={user}
                rows={rows}
                live={live}
                connected={connected}
                onToggle={handleToggle}
                thicknessState={thicknessState}
                onApplyCalibration={handleApplyCalibration}
                onResetCalibration={handleResetCalibration}
                calibrationBusy={calibrationBusy}
                runModeVisitKey={runModeVisitKey}
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