import { useState, useRef, useEffect } from "react";
import { Ic } from "../../icons/Icons";
import { ROLE_ACCESS } from "../../constants/roles";
import AccessDenied from "../../components/AccessDenied";
import { SERVER } from "../../constants/config";
import { SENSOR_CONFIGS } from "../../constants/config_opposite";

export default function RunModePage({
  user,
  rows,
  live,
  connected,
  onToggle,
  thicknessState,
  onSetGapDistance,
  onSetAutoGap,
  onResetGap,
  calibrationBusy,
  runModeVisitKey,
}) {
  if (!ROLE_ACCESS[user.role]?.includes("run-mode")) return <AccessDenied />;

  const [showGapDialog, setShowGapDialog] = useState(false);
  const [gapValue, setGapValue] = useState("");
  const [gapError, setGapError] = useState("");
  const [limitActive, setLimitActive] = useState(false);
  const [minLimit, setMinLimit] = useState("");
  const [maxLimit, setMaxLimit] = useState("");

  // Auto-gap state
  const [autoGapMode, setAutoGapMode] = useState(false);
  const [objectThickness, setObjectThickness] = useState("");
  const [toleranceRange, setToleranceRange] = useState("");

  const gapDistance = thicknessState?.gap_distance || 0;
  const calibrationActive = Boolean(thicknessState?.calibration_active);
  const autoGapActive = Boolean(thicknessState?.auto_gap_active);
  const isCalibrated = calibrationActive || Boolean(thicknessState?.calibration_completed);

  const canvasRef = useRef(null);
  const WINDOW = 100;

  const latest = rows[0];
  const latestThickness = latest?.thickness ?? null;
  const sensorsOutOfRange = latestThickness !== null && parseFloat(latestThickness) === 0;

  // Get last 100 thickness readings for the history table
  const thicknessHistory = [...rows].reverse().slice(0, 100).filter(r => r.thickness !== null);


  useEffect(() => {
    if (!isCalibrated) {
      setShowGapDialog(true);
    } else {
      setShowGapDialog(false);
    }
  }, [runModeVisitKey]);

  // Load saved tolerance values from thicknessState when available
  useEffect(() => {
    if (autoGapActive && thicknessState) {
      if (thicknessState.thickness_tolerance_min !== null && thicknessState.thickness_tolerance_min !== undefined) {
        setMinLimit(String(thicknessState.thickness_tolerance_min));
      }
      if (thicknessState.thickness_tolerance_max !== null && thicknessState.thickness_tolerance_max !== undefined) {
        setMaxLimit(String(thicknessState.thickness_tolerance_max));
      }
      setLimitActive(true);
    }
  }, [autoGapActive, thicknessState]);

  function formatValue(value) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return "0";
    return numericValue.toFixed(3);
  }

  function calcDistance(sensorReading) {
    const n = Number(sensorReading);
    if (!Number.isFinite(n)) return null;
    return n + 35;
  }

  function computeToleranceLimits(thickness, rangeVal) {
    if (!rangeVal) return { tolMin: null, tolMax: null };
    const r = Number(rangeVal);
    if (!Number.isFinite(r)) return { tolMin: null, tolMax: null };
    return {
      tolMin: thickness - r,
      tolMax: thickness + r,
    };
  }

  async function handleSubmitGap(e) {
    e.preventDefault();
    
    // Compute tolerance limits from the range value (if provided)
    const usedThickness = autoGapMode ? Number(objectThickness) : null;
    const { tolMin, tolMax } = computeToleranceLimits(usedThickness || 0, toleranceRange);

    if (autoGapMode) {
      // Auto-gap mode
      const parsedThickness = Number(objectThickness);
      if (!Number.isFinite(parsedThickness) || parsedThickness <= 0) {
        setGapError("Enter a valid object thickness greater than zero.");
        return;
      }
      const success = await onSetAutoGap(parsedThickness, tolMin, tolMax);
      if (success) {
        setShowGapDialog(false);
        setGapValue("");
        setGapError("");
        // Apply tolerance limits immediately
        if (tolMin !== null) setMinLimit(String(tolMin));
        if (tolMax !== null) setMaxLimit(String(tolMax));
        setLimitActive(tolMin !== null);
      }
    } else {
      // Manual gap mode
      const parsed = Number(gapValue);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setGapError("Enter a valid distance greater than zero.");
        return;
      }
      const success = await onSetGapDistance(parsed);
      if (success) {
        setShowGapDialog(false);
        setGapValue("");
        setGapError("");
      }
    }
  }

  function handleReset() {
    onResetGap();
    setShowGapDialog(true);
    setAutoGapMode(false);
    setObjectThickness("");
    setToleranceRange("");
    setMinLimit("");
    setMaxLimit("");
    setLimitActive(false);
  }

  function getThicknessColor(v) {
    if (v === null || v === undefined) return "var(--text-3)";
    if (!limitActive) return "var(--green)";
    const n = parseFloat(v);
    const mn = parseFloat(minLimit);
    const mx = parseFloat(maxLimit);
    if (!isNaN(mn) && n < mn) return "var(--red)";
    if (!isNaN(mx) && n > mx) return "var(--red)";
    return "var(--green)";
  }

  function drawThicknessGraph(canvas) {
    if (!canvas) return;
    const ctx   = canvas.getContext("2d");
    const W     = canvas.width;
    const H     = canvas.height;
    const slice = [...rows].reverse().slice(0, WINDOW);
    const vals  = slice.map(r => parseFloat(r.thickness)).filter(n => !isNaN(n));

    ctx.clearRect(0, 0, W, H);

    if (vals.length < 2) {
      ctx.fillStyle = "#8e97ab";
      ctx.font      = "11px 'JetBrains Mono', monospace";
      ctx.textAlign = "center";
      ctx.fillText("Waiting for data…", W / 2, H / 2);
      return;
    }

    const mn    = parseFloat(minLimit);
    const mx    = parseFloat(maxLimit);
    const min   = Math.min(...vals) - 0.5;
    const max   = Math.max(...vals) + 0.5;
    const pad   = { top: 10, bottom: 20, left: 36, right: 10 };
    const gW    = W - pad.left - pad.right;
    const gH    = H - pad.top - pad.bottom;
    const total = vals.length;

    function xPos(i) { return pad.left + (i / (total - 1)) * gW; }
    function yPos(v) { return pad.top + (1 - (v - min) / (max - min)) * gH; }

    // Grid lines
    ctx.strokeStyle = "#dfe2e9";
    ctx.lineWidth   = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (i / 4) * gH;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(W - pad.right, y);
      ctx.stroke();
      const label = (max - (i / 4) * (max - min)).toFixed(2);
      ctx.fillStyle  = "#8e97ab";
      ctx.font       = "9px 'JetBrains Mono', monospace";
      ctx.textAlign  = "right";
      ctx.fillText(label, pad.left - 4, y + 3);
    }

    // Min limit line
    if (limitActive && !isNaN(mn) && mn >= min && mn <= max) {
      ctx.strokeStyle = "rgba(236, 7, 7, 0.6)";
      ctx.lineWidth   = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(pad.left, yPos(mn));
      ctx.lineTo(W - pad.right, yPos(mn));
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Max limit line
    if (limitActive && !isNaN(mx) && mx >= min && mx <= max) {
      ctx.strokeStyle = "rgba(255, 4, 4, 0.6)";
      ctx.lineWidth   = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(pad.left, yPos(mx));
      ctx.lineTo(W - pad.right, yPos(mx));
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Line
    ctx.beginPath();
    vals.forEach((v, i) => {
      const x = xPos(i);
      const y = yPos(v);
      if (i === 0) ctx.moveTo(x, y);
      else         ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#3B55A8";
    ctx.lineWidth   = 1.5;
    ctx.stroke();

    // Dots
    vals.forEach((v, i) => {
      const inLimit = !limitActive || (
        (isNaN(mn) || v >= mn) && (isNaN(mx) || v <= mx)
      );
      ctx.beginPath();
      ctx.arc(xPos(i), yPos(v), 2, 0, Math.PI * 2);
      ctx.fillStyle = inLimit ? "#3B55A8" : "#dc3232";
      ctx.fill();
    });
  }

  useEffect(() => {
    drawThicknessGraph(canvasRef.current);
  }, [rows, limitActive, minLimit, maxLimit]);

  return (
    <div className="fade-up" style={{ display: "flex", flexDirection: "column", flex: 1 }}>
      {/* GAP DISTANCE DIALOG */}
      {showGapDialog && (
        <div className="dialog-overlay" role="presentation">
          <div className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="gap-dialog-title">
            <form onSubmit={handleSubmitGap}>
              <div className="dialog-title" id="gap-dialog-title">Sensor Setup</div>
              
              {/* Mode Toggle */}
              <div style={{
                display: "flex", gap: 8, marginBottom: 16,
                background: "var(--bg2)", borderRadius: "var(--r)",
                padding: 4,
              }}>
                <button
                  type="button"
                  className={!autoGapMode ? "btn btn-blue btn-sm" : "btn btn-sm btn-outline"}
                  style={{ flex: 1, justifyContent: "center" }}
                  onClick={() => { setAutoGapMode(false); setGapError(""); }}
                >
                  Manual Gap
                </button>
                <button
                  type="button"
                  className={autoGapMode ? "btn btn-blue btn-sm" : "btn btn-sm btn-outline"}
                  style={{ flex: 1, justifyContent: "center" }}
                  onClick={() => { setAutoGapMode(true); setGapError(""); }}
                >
                  Auto-Gap
                </button>
              </div>

              {!autoGapMode ? (
                /* ── Manual Gap Mode ── */
                <>
                  <div className="dialog-text">
                    <strong>Note:</strong>
                    <br />
                    Place both sensors at their fixed positions (opposite each other).
                    <br />
                    Enter the distance between the two sensor faces in millimeters.
                  </div>
                   <input
                     type="number"
                     step="0.001"
                     min="0.001"
                     className="form-input dialog-input"
                     placeholder="Enter distance between two sensors (mm)"
                     value={gapValue}
                     onChange={(e) => setGapValue(e.target.value)}
                     autoFocus
                   />
                 </>
              ) : (
                /* ── Auto-Gap Mode ── */
                <>
                  <div className="dialog-text" style={{ marginBottom: 4 }}>
                    <strong>Auto-Gap Calculation:</strong>
                  </div>

                  {/* Object Thickness */}
                  <div style={{ marginBottom: 12 }}>
                    <label style={{
                      fontSize: 12, fontWeight: 600, color: "var(--text-2)",
                      fontFamily: "var(--mono)", marginBottom: 6, display: "block",
                    }}>
                      Enter the thickness of the object
                    </label>
                    <input
                      type="number"
                      step="0.001"
                      min="0.001"
                      className="form-input"
                      placeholder="e.g. 5.000 (mm)"
                      value={objectThickness}
                      onChange={(e) => setObjectThickness(e.target.value)}
                      style={{ fontFamily: "var(--mono)", width: "100%" }}
                      autoFocus
                    />
                  </div>

                  {/* Tolerance Range for Auto-Gap */}
                  <div style={{
                    padding: "12px 14px",
                    background: "var(--bg2)",
                    borderRadius: "var(--r)",
                    border: "1px solid var(--border)",
                  }}>
                    <div style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: "var(--text-3)",
                      fontFamily: "var(--mono)",
                      marginBottom: 6,
                      textTransform: "uppercase",
                      letterSpacing: "0.5px",
                    }}>
                      Thickness Limit (Tolerance)
                    </div>
                    <input
                      type="number"
                      step="0.1"
                      className="form-input"
                      placeholder="e.g. 0.2 (mm)"
                      value={toleranceRange}
                      onChange={(e) => setToleranceRange(e.target.value)}
                      style={{ fontFamily: "var(--mono)", width: "100%" }}
                    />
                    {toleranceRange && objectThickness && (
                      <div style={{
                        marginTop: 6,
                        padding: "4px 10px",
                        borderRadius: 6,
                        fontSize: 11,
                        fontFamily: "var(--mono)",
                        background: "var(--blue-ghost)",
                        color: "var(--blue)",
                        border: "1px solid rgba(59,85,168,0.2)",
                      }}>
                        Limits: {(Number(objectThickness) - Number(toleranceRange)).toFixed(3)} mm — {(Number(objectThickness) + Number(toleranceRange)).toFixed(3)} mm
                      </div>
                    )}
                  </div>
                </>
              )}

              {gapError && <div className="dialog-error">{gapError}</div>}
              <div className="dialog-actions">
                <button type="submit" className="btn btn-green" disabled={calibrationBusy}>
                  {calibrationBusy ? "Saving..." : "Set"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* PAGE HEADER */}
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <div className="page-title">Live Run Mode</div>
            <div className="page-sub">OPPOSITE SENSORS - THICKNESS MEASUREMENT</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {live && connected && <div className="live-dot"><div className="dot" /> LIVE</div>}
          </div>
        </div>
      </div>

      {/* CONNECTION STATUS */}
      <div style={{ padding: "0 32px", marginBottom: 16 }}>
        <div style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          background: connected ? "var(--green-ghost)" : "var(--bg2)",
          border: connected ? "1px solid rgba(45,122,79,0.25)" : "1px solid var(--border)",
          borderRadius: "var(--r)",
          padding: "6px 12px",
          fontSize: 12,
          fontFamily: "var(--mono)",
          color: connected ? "var(--green)" : "var(--text-3)",
        }}>
          <span style={{
            width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
            background: connected ? "var(--green)" : "var(--text-3)",
          }} />
          {connected ? "Connected" : "Disconnected · Waiting for server connection..."}
        </div>
      </div>

      {/* SENSORS OUT OF RANGE NOTICE */}
      {sensorsOutOfRange && (
        <div style={{
          padding: "0 32px",
          marginBottom: 16,
        }}>
          <div style={{
            padding: "14px 20px",
            borderRadius: "var(--r2)",
            background: "linear-gradient(135deg, rgba(178,73,73,0.1), rgba(178,73,73,0.03))",
            border: "1px solid rgba(178,73,73,0.2)",
            display: "flex",
            alignItems: "center",
            gap: 12,
            fontSize: 14,
            fontWeight: 600,
            color: "var(--red)",
          }}>
            <span style={{ fontSize: 20, lineHeight: 1 }}>⚠</span>
            <span>Status: Sensors are out of range</span>
            <span style={{
              marginLeft: "auto", fontSize: 12, fontWeight: 400,
              fontFamily: "var(--mono)", color: "var(--text-3)",
            }}>
              Thickness: 0.000 mm
            </span>
          </div>
        </div>
      )}

      {/* GAP DISTANCE STATUS + RESET */}
      <div className="section">
        <div className="section-header">
          <span className="section-title">Sensor Gap Configuration</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {autoGapActive && (
              <span style={{
                fontSize: 11, fontFamily: "var(--mono)", color: "var(--blue)",
                background: "var(--blue-ghost)", padding: "3px 10px",
                borderRadius: "var(--r)", border: "1px solid rgba(59,85,168,0.2)",
              }}>
                Auto-Gap Mode
              </span>
            )}
            <button className="btn btn-sm btn-outline" onClick={handleReset} disabled={calibrationBusy}>
              <Ic.Refresh /> Reset Gap
            </button>
          </div>
        </div>
        <div style={{
          background: "linear-gradient(135deg, rgba(45,122,79,0.06), rgba(59,85,168,0.04))",
          border: "1px solid var(--border)",
          borderRadius: "var(--r2)",
          padding: "16px 18px",
          display: "grid",
          gap: 10,
        }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text)" }}>
            {isCalibrated
              ? "Total Gap: " + formatValue(gapDistance) + " mm"
              : "Gap not configured - enter the distance between sensors"}
          </div>
        </div>
      </div>

      {/* THICKNESS VALUE BOX */}
      <div className="section">
        <div className="section-header">
          <span className="section-title">Calculated Thickness</span>
        </div>
        <div style={{
          background: "linear-gradient(135deg, rgba(45,122,79,0.08), rgba(45,122,79,0.02))",
          border: "2px solid rgba(45,122,79,0.25)",
          borderRadius: "var(--r2)",
          padding: "24px 32px",
          textAlign: "center",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 4,
        }}>
          <div style={{ fontSize: 11, color: "var(--text-3)", fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "1px" }}>
            Object Thickness
          </div>
          <div style={{
            fontSize: 64,
            fontWeight: 800,
            fontFamily: "var(--mono)",
            color: getThicknessColor(latestThickness),
            lineHeight: 1,
            letterSpacing: "-2px",
          }}>
            {latestThickness !== null ? formatValue(latestThickness) : "-"}
          </div>
          <div style={{ fontSize: 16, color: "var(--text-2)", fontFamily: "var(--mono)" }}>
            mm
          </div>

        </div>
      </div>

      {/* SENSOR DISTANCE INDIVIDUAL CARDS */}
      <div className="stats-grid">
        {Object.keys(SENSOR_CONFIGS).map((sid) => {
          const key = sid === "A" ? "a" : "b";
          const value = latest?.[key] ?? null;
          const online = value !== null;
          const dotClass = "s-dot " + (connected && online ? "on" : "off");
          return (
            <div key={sid} className="stat-card">
              <div className="stat-label">
                <span className={dotClass} style={{ display: "inline-block" }} />
                &nbsp;Sensor {sid} Distance
              </div>
              <div className="stat-val" style={{ fontSize: 28, color: online ? "var(--blue)" : "var(--text-3)" }}>
                {value !== null ? formatValue(calcDistance(value)) : "-"}
              </div>
              <div className="stat-sub">mm</div>
            </div>
          );
        })}
      </div>

      {/* LIMIT SETTINGS */}
      <div className="section">
        <div className="section-header">
          <span className="section-title">Thickness Limit</span>
          <button
            className={limitActive ? "btn btn-sm btn-red" : "btn btn-sm btn-outline"}
            onClick={() => setLimitActive((current) => !current)}
          >
            {limitActive ? "Disable Limit" : "Enable Limit"}
          </button>
        </div>
        <div style={{
          background: "var(--bg2)", border: "1px solid var(--border)",
          borderRadius: "var(--r2)", padding: "16px 18px",
          display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap",
        }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 5, fontFamily: "var(--mono)" }}>MIN (mm)</div>
            <input type="number" className="form-input" placeholder="e.g. 4"
              value={minLimit} onChange={(e) => setMinLimit(e.target.value)}
              style={{ width: 100, fontFamily: "var(--mono)" }} />
          </div>
          <div style={{ color: "var(--text-3)", fontSize: 18, marginTop: 16 }}>-</div>
          <div>
            <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 5, fontFamily: "var(--mono)" }}>MAX (mm)</div>
            <input type="number" className="form-input" placeholder="e.g. 8"
              value={maxLimit} onChange={(e) => setMaxLimit(e.target.value)}
              style={{ width: 100, fontFamily: "var(--mono)" }} />
          </div>
          {limitActive && (
            <div style={{
              marginLeft: "auto", background: "var(--blue-ghost)",
              border: "1px solid rgba(59,85,168,0.2)", borderRadius: "var(--r)",
              padding: "8px 14px", fontSize: 12, fontFamily: "var(--mono)", color: "var(--blue)",
            }}>
              Active: {minLimit || "-"} mm to {maxLimit || "-"} mm
            </div>
          )}
        </div>
      </div>

      {/* LIVE THICKNESS GRAPH */}
      <div className="section">
        <div className="section-header">
          <span className="section-title">Live Graph - Last 100 Thickness Samples</span>
        </div>
        <div style={{
          background: "var(--bg2)", border: "1px solid var(--border)",
          borderRadius: "var(--r2)", overflow: "hidden",
        }}>
          <div style={{
            padding: "10px 14px", borderBottom: "1px solid var(--border)",
            display: "flex", alignItems: "center", justifyContent: "space-between",
          }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
              Thickness Over Time
            </span>
            <span style={{
              fontSize: 13, fontFamily: "var(--mono)", fontWeight: 700,
              color: getThicknessColor(latestThickness),
            }}>
              {latestThickness !== null ? formatValue(latestThickness) + " mm" : "-"}
            </span>
          </div>
          <canvas
            ref={canvasRef}
            width={800}
            height={200}
            style={{ width: "100%", height: 200, display: "block" }}
          />
        </div>
      </div>

      {/* THICKNESS HISTORY TABLE */}
      <div className="section">
        <div className="section-header">
          <span className="section-title">Thickness History (Last 100 Readings)</span>
          <span style={{ fontSize: 11, color: "var(--text-3)", fontFamily: "var(--mono)" }}>
            {thicknessHistory.length} records
          </span>
        </div>

        {thicknessHistory.length === 0 ? (
          <div style={{
            background: "var(--bg2)", border: "1px solid var(--border)",
            borderRadius: "var(--r2)", padding: "48px 32px",
            textAlign: "center", color: "var(--text-3)",
            fontFamily: "var(--mono)", fontSize: 13,
          }}>
            No thickness data yet — configure the sensor gap and waiting for readings...
          </div>
        ) : (
          <div className="table-wrap scroll-table" style={{ maxHeight: 400 }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 50 }}>#</th>
                  <th>Timestamp</th>
                  <th className="td-r">Sensor A Dist (mm)</th>
                  <th className="td-r">Sensor B Dist (mm)</th>
                  <th className="td-r" style={{ color: "var(--green)" }}>Thickness (mm)</th>
                </tr>
              </thead>
              <tbody>
                {thicknessHistory.map((r) => (
                  <tr key={r.id}>
                    <td className="td-mono td-dim">{r.id}</td>
                    <td className="td-mono td-dim" style={{ fontSize: 11 }}>{r.ts}</td>
                    <td className="td-mono td-r">
                      {r.a !== null ? formatValue(calcDistance(r.a)) : <span style={{ color: "var(--text-3)" }}>-</span>}
                    </td>
                    <td className="td-mono td-r">
                      {r.b !== null ? formatValue(calcDistance(r.b)) : <span style={{ color: "var(--text-3)" }}>-</span>}
                    </td>
                    <td className="td-mono td-r" style={{ fontWeight: 700, color: getThicknessColor(r.thickness) }}>
                      {r.thickness !== null ? formatValue(r.thickness) : <span style={{ color: "var(--text-3)" }}>-</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}