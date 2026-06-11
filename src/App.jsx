import { useState, useRef, useEffect } from "react";
import "./styles/global.css";
import { io } from "socket.io-client";

import Topbar  from "./layout/Topbar";
import Sidebar from "./layout/Sidebar";
import Toast   from "./components/Toast";

import LoginPage        from "./pages/LoginPage";
import DashboardPage    from "./pages/DashboardPage";
import SensorConfigPage from "./pages/SensorConfigPage";
import RunModePage      from "./pages/RunModePage";
import DownloadPage     from "./pages/DownloadPage";
import BackendPage      from "./pages/BackendPage";

import { SERVER } from "./constants/config";

export default function App() {
  const [user,       setUser]       = useState(null);
  const [page,       setPage]       = useState("dashboard");
  const [toast,      setToast]      = useState(null);
  const [live,       setLive]       = useState(false);
  const [connected,  setConnected]  = useState(false);
  const [rows,       setRows]       = useState([]);
  const [streamRate, setStreamRate] = useState(null);
  const [thicknessState, setThicknessState] = useState(null);
  const [calibrationBusy, setCalibrationBusy] = useState(false);
  const [runModeVisitKey, setRunModeVisitKey] = useState(0);

  const socketRef       = useRef(null);
  const counterRef      = useRef(1);
  const dataBufferRef   = useRef([]);
  const lastReadingTime = useRef(null);

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

      // Fetch full thickness state after calibration to avoid partial-state overwrite
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
      const row = {
        id: counterRef.current++,
        ts: data.timestamp
          ? data.timestamp.replace("T", " ").slice(0, 23)
          : new Date().toISOString().replace("T", " ").slice(0, 23),
        a: data.sensor_A ?? null,
        b: data.sensor_B ?? null,
        c: data.sensor_C ?? null,
      };

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

  useEffect(() => {
    if (!user) return;
    loadThicknessState();
  }, [user]);

  useEffect(() => {
    if (page === "run-mode") {
      setRunModeVisitKey(key => key + 1);
    }
  }, [page]);

  useEffect(() => {
    return () => {
      if (socketRef.current) socketRef.current.disconnect();
    };
  }, []);

  if (!user) return <LoginPage onLogin={handleLogin} />;

  return (
    <div className="app-shell">
      <Topbar user={user} page={page} onLogout={handleLogout} />

      <div className="content-area">
        <Sidebar user={user} page={page} onNavigate={setPage} onLogout={handleLogout} />

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
            <RunModePage
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