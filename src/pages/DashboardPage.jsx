import { useState, useEffect } from "react";
import { Ic } from "../icons/Icons";
import { ROLE_ACCESS } from "../constants/roles";
import { SENSOR_CONFIGS } from "../constants/config";

export default function DashboardPage({ user, onNavigate, rows, streamRate }) {
  const access = ROLE_ACCESS[user.role] || [];

  const [sensorStatus, setSensorStatus] = useState(() => {
    const s = {};
    Object.keys(SENSOR_CONFIGS).forEach(sid => s[sid] = false);
    return s;
  });

  useEffect(() => {
    if (!rows || rows.length === 0) return;
    const latest = rows[0];
    const newStatus = {};
    Object.keys(SENSOR_CONFIGS).forEach(sid => {
      const val = latest[sid.toLowerCase()];
      newStatus[sid] = val !== null && val !== undefined;
    });
    setSensorStatus(newStatus);
  }, [rows]);

  const onlineCount = Object.values(sensorStatus).filter(Boolean).length;
  const totalCount  = Object.keys(SENSOR_CONFIGS).length;
  const allOnline   = onlineCount === totalCount && totalCount > 0;
  const anyOnline   = onlineCount > 0;

  const displayRate  = streamRate || "—";
  const msInterval   = streamRate ? `${Math.round(1000 / parseFloat(streamRate))}ms interval` : "Start Run Mode";

  const tiles = [
    {
      id:    "sensor-config",
      label: "Sensor Config",
      desc:  "Configure sensor parameters, thresholds and sampling rate.",
      icon:  <Ic.Sensor />,
      color: "var(--blue)",
      bg:    "rgba(59,85,168,0.06)",
    },
    {
      id:    "run-mode",
      label: "Live Run Mode",
      desc:  "Monitor real-time thickness data from all active sensors.",
      icon:  <Ic.Activity />,
      color: "var(--green)",
      bg:    "rgba(74,122,94,0.06)",
    },
    {
      id:    "download",
      label: "Download Data",
      desc:  "Export filtered or raw sensor readings as CSV files.",
      icon:  <Ic.Download />,
      color: "var(--amber)",
      bg:    "rgba(122,120,80,0.06)",
    },
    {
      id:    "backend",
      label: "Backend Access",
      desc:  "System configuration, server code and database management.",
      icon:  <Ic.Backend />,
      color: "var(--blue)",
      bg:    "rgba(59,85,168,0.06)",
    },
  ].filter(t => access.includes(t.id));

  return (
    <div className="fade-up">

      {/* PAGE HEADER */}
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <div className="page-title">Welcome back, {user.username}</div>
            <div className="page-sub">
              THICKNESS MONITORING DASHBOARD ·{" "}
              {new Date().toLocaleDateString("en-US", {
                weekday: "long",
                year:    "numeric",
                month:   "long",
                day:     "numeric",
              })}
            </div>
          </div>
        </div>
      </div>

      {/* STAT CARDS */}
      <div className="stats-grid">

        <div className="stat-card">
          <div className="stat-label"><Ic.Wifi /> System Status</div>
          <div
            className="stat-val"
            style={{
              color: allOnline
                ? "var(--green)"
                : anyOnline
                ? "var(--amber)"
                : "var(--red)",
            }}
          >
            {allOnline ? "Online" : anyOnline ? "Partial" : "Offline"}
          </div>
          <div className="stat-sub">
            {`${onlineCount} of ${totalCount} sensors active`}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-label"><Ic.Sensor /> Active Sensors</div>
          <div className="stat-val blue">{onlineCount} / {totalCount}</div>
          <div className="stat-sub">
            {Object.entries(sensorStatus)
              .filter(([, v]) => v)
              .map(([k]) => k)
              .join(" · ") || "None — start Run Mode"}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-label"><Ic.Activity /> Stream Rate</div>
          <div className="stat-val">
            {displayRate}{streamRate ? " Hz" : ""}
          </div>
          <div className="stat-sub">{msInterval}</div>
        </div>

        <div className="stat-card">
          <div className="stat-label"><Ic.Shield /> Access Level</div>
          <div className="stat-val amber">{user.role}</div>
          <div className="stat-sub">{access.length} modules available</div>
        </div>

      </div>

      {/* SENSOR STATUS PILLS */}
      <div className="sensor-status-row">
        {Object.entries(SENSOR_CONFIGS).map(([sid, cfg]) => {
          const online = sensorStatus[sid];
          return (
            <div key={sid} className={`sensor-pill ${online ? "online" : "offline"}`}>
              <div className={`s-dot ${online ? "on" : "off"}`} />
              Sensor {sid} · {cfg.ip} · {online ? "Online" : "Offline"}
            </div>
          );
        })}
      </div>

      {/* NAV TILES */}
      <div className="nav-grid">
        {tiles.map(t => (
          <div
            key={t.id}
            className="nav-tile"
            style={{ "--tile-color": t.color, "--tile-bg": t.bg }}
            onClick={() => onNavigate(t.id)}
          >
            <div className="tile-icon" style={{ background: t.bg, color: t.color }}>
              {t.icon}
            </div>
            <div className="tile-name">{t.label}</div>
            <div className="tile-desc">{t.desc}</div>
            <span className="tile-arrow"><Ic.ChevRight /></span>
          </div>
        ))}
      </div>

    </div>
  );
}