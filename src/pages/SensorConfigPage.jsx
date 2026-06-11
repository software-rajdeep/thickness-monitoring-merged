import { useState } from "react";
import { Ic } from "../icons/Icons";
import { ROLE_ACCESS } from "../constants/roles";
import AccessDenied from "../components/AccessDenied";
import Spinner from "../components/Spinner";
import { SERVER } from "../constants/config";

const REG = {
  sampling:  { addr_h: "0x40", addr_l: "0x06" },
  averaging: { addr_h: "0x40", addr_l: "0x0A" },
  polarity:  { addr_h: "0x40", addr_l: "0x08" },
  alarm:     { addr_h: "0x40", addr_l: "0x0C" },
};

const SP_VALS  = ["0x00","0x01","0x02","0x03","0x0A"];
const SP_LABEL = ["500us","1000us","2000us","4000us","AUTO"];
const SP_JSON  = ["500us","1000us","2000us","4000us","AUTO"];

const AV_VALS  = ["0x00","0x01","0x02","0x03"];
const AV_LABEL = ["1","8","64","512"];
const AV_JSON  = ["1","8","64","512"];

const OP_VALS  = ["0x00","0x01"];
const OP_LABEL = ["Light_ON","Dark_ON"];

const AL_VALS  = ["0x00","0x01"];
const AL_LABEL = ["Clamp","Hold"];

const SENSOR_IDS = ["A","B","C"];

export default function SensorConfigPage({ user, onToast }) {
  if (!ROLE_ACCESS[user.role]?.includes("sensor-config")) return <AccessDenied />;

  // ── Shared config — single set of parameters applied to ALL sensors ──
  const [sharedConfig, setSharedConfig] = useState({
    sampling: "0",
    averaging: "2",
    polarity: "0",
    alarm: "0",
  });

  const [streamRate, setStreamRate] = useState("5");
  const [saving,     setSaving]     = useState(false);
  const [busyStream, setBusyStream] = useState(false);
  const [rawFields,  setRawFields]  = useState({ sensor: "A", addr_h: "", addr_l: "", val_h: "", val_l: "" });
  const [rawLoading, setRawLoading] = useState(false);
  const [log,        setLog]        = useState([{ type: "sys", msg: "System ready." }]);

  function addLog(msg, type = "def") {
    const ts = new Date().toLocaleTimeString("en-GB", { hour12: false });
    setLog(prev => [...prev, { ts, msg, type }]);
  }

  function updateShared(key, val) {
    setSharedConfig(c => ({ ...c, [key]: val }));
  }

  // ── LOAD CONFIG ──────────────────────────────────────────────────────────
  async function loadConfig() {
    try {
      const res = await fetch(`${SERVER}/config/file`);
      if (!res.ok) { addLog("Could not fetch config file", "err"); return; }
      const cfg = await res.json();

      if (cfg.global_settings?.stream_rate_hz)
        setStreamRate(String(cfg.global_settings.stream_rate_hz));

      const SP_MAP = { "500us":"0","1000us":"1","2000us":"2","4000us":"3","AUTO":"4" };
      const AV_MAP = { "1":"0","8":"1","64":"2","512":"3" };
      const OP_MAP = { "Light_ON":"0","Dark_ON":"1" };
      const AL_MAP = { "Clamp":"0","Hold":"1" };

      // Load from first available sensor; all should be identical
      let loaded = false;
      for (const sid of SENSOR_IDS) {
        const key = `sensor_${sid}`;
        if (!cfg[key]) continue;
        const s = cfg[key];
        setSharedConfig({
          sampling:  SP_MAP[s.sampling_period] ?? "0",
          averaging: AV_MAP[String(s.averaging)] ?? "2",
          polarity:  OP_MAP[s.output_polarity] ?? "0",
          alarm:     AL_MAP[s.alarm_output] ?? "0",
        });
        loaded = true;
        break;
      }
      if (!loaded) {
        addLog("No sensor config found in file — using defaults", "inf");
      }
      addLog("Config loaded from server", "ok");
      onToast("Configuration loaded", "success");
    } catch (e) {
      addLog(`Load error: ${e.message}`, "err");
      onToast("Failed to load config", "error");
    }
  }

  // ── WRITE HARDWARE (for a single sensor) ─────────────────────────────────
  async function writeOneHW(sid, addr_h, addr_l, val_l) {
    const res = await fetch(`${SERVER}/config/write`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ sensor: sid, addr_h, addr_l, val_h: "0x00", val_l }),
    });
    return res.json();
  }

  // ── UPDATE JSON FILE FOR ALL SENSORS ─────────────────────────────────────
  async function updateAllJSON(spIdx, avIdx, opIdx, alIdx) {
    const getRes = await fetch(`${SERVER}/config/file`);
    const cfg    = await getRes.json();
    for (const sid of SENSOR_IDS) {
      const key = `sensor_${sid}`;
      if (!cfg[key]) cfg[key] = {};
      cfg[key].sampling_period = SP_JSON[spIdx];
      cfg[key].averaging       = parseInt(AV_JSON[avIdx]);
      cfg[key].output_polarity = OP_LABEL[opIdx];
      cfg[key].alarm_output    = AL_LABEL[alIdx];
    }
    await fetch(`${SERVER}/config/file`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(cfg),
    });
  }

  // ── WRITE CONFIG TO ALL SENSORS ──────────────────────────────────────────
  async function writeAllSensors(spIdx, avIdx, opIdx, alIdx) {
    let allOk = true;

    for (const sid of SENSOR_IDS) {
      try {
        addLog(`[${sid}] Writing Sampling → ${SP_LABEL[spIdx]}`, "inf");
        const r1 = await writeOneHW(sid, REG.sampling.addr_h, REG.sampling.addr_l, SP_VALS[spIdx]);
        if (r1.message) addLog(`[${sid}] ✓ Sampling = ${SP_LABEL[spIdx]}`, "ok");
        else { addLog(`[${sid}] ✗ Sampling failed`, "err"); allOk = false; }

        addLog(`[${sid}] Writing Averaging → ${AV_LABEL[avIdx]}`, "inf");
        const r2 = await writeOneHW(sid, REG.averaging.addr_h, REG.averaging.addr_l, AV_VALS[avIdx]);
        if (r2.message) addLog(`[${sid}] ✓ Averaging = ${AV_LABEL[avIdx]}`, "ok");
        else { addLog(`[${sid}] ✗ Averaging failed`, "err"); allOk = false; }

        addLog(`[${sid}] Writing Polarity → ${OP_LABEL[opIdx]}`, "inf");
        const r3 = await writeOneHW(sid, REG.polarity.addr_h, REG.polarity.addr_l, OP_VALS[opIdx]);
        if (r3.message) addLog(`[${sid}] ✓ Polarity = ${OP_LABEL[opIdx]}`, "ok");
        else { addLog(`[${sid}] ✗ Polarity failed`, "err"); allOk = false; }

        addLog(`[${sid}] Writing Alarm → ${AL_LABEL[alIdx]}`, "inf");
        const r4 = await writeOneHW(sid, REG.alarm.addr_h, REG.alarm.addr_l, AL_VALS[alIdx]);
        if (r4.message) addLog(`[${sid}] ✓ Alarm = ${AL_LABEL[alIdx]}`, "ok");
        else { addLog(`[${sid}] ✗ Alarm failed`, "err"); allOk = false; }

        await new Promise(r => setTimeout(r, 300));
      } catch (e) {
        addLog(`[${sid}] ✗ Error: ${e.message}`, "err");
        allOk = false;
      }
    }

    return allOk;
  }

  // ── SAVE (shared config → all sensors) ───────────────────────────────────
  async function handleSave() {
    setSaving(true);
    addLog("──── Save All started ────", "sys");

    const spIdx = parseInt(sharedConfig.sampling);
    const avIdx = parseInt(sharedConfig.averaging);
    const opIdx = parseInt(sharedConfig.polarity);
    const alIdx = parseInt(sharedConfig.alarm);

    let allOk = await writeAllSensors(spIdx, avIdx, opIdx, alIdx);

    if (allOk) {
      await updateAllJSON(spIdx, avIdx, opIdx, alIdx);
    }

    try {
      const hz  = parseFloat(streamRate);
      const res = await fetch(`${SERVER}/stream/config`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ rate: hz }),
      });
      const d = await res.json();
      if (d.message) addLog(`[GLOBAL] ✓ Stream Rate = ${hz} Hz`, "ok");
      else { addLog(`[GLOBAL] ✗ Stream rate failed`, "err"); allOk = false; }
    } catch (e) {
      addLog(`[GLOBAL] ✗ Stream rate error`, "err");
      allOk = false;
    }

    addLog("──── Save All complete ────", "sys");
    setSaving(false);
    onToast(allOk ? "Configuration saved successfully" : "Some writes failed — check log", allOk ? "success" : "error");
  }

  // ── APPLY STREAM RATE ────────────────────────────────────────────────────
  async function applyStreamRate() {
    setBusyStream(true);
    const hz = parseFloat(streamRate);
    try {
      const res  = await fetch(`${SERVER}/stream/config`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ rate: hz }),
      });
      const data = await res.json();
      if (data.message) {
        addLog(`[GLOBAL] ✓ Stream Rate = ${hz} Hz`, "ok");
        const getRes = await fetch(`${SERVER}/config/file`);
        const cfg    = await getRes.json();
        cfg.global_settings.stream_rate_hz = hz;
        await fetch(`${SERVER}/config/file`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(cfg),
        });
        onToast(`Stream rate set to ${hz} Hz`, "success");
      } else {
        addLog(`[GLOBAL] ✗ Failed: ${data.error}`, "err");
        onToast("Stream rate update failed", "error");
      }
    } catch (e) {
      addLog(`[GLOBAL] ✗ Network error`, "err");
      onToast("Network error", "error");
    }
    setBusyStream(false);
  }

  // ── RAW WRITE ────────────────────────────────────────────────────────────
  async function handleExecuteWrite() {
    const { sensor, addr_h, addr_l, val_h, val_l } = rawFields;
    if (!addr_h || !addr_l || !val_h || !val_l) {
      onToast("Please fill all four fields", "error"); return;
    }
    setRawLoading(true);
    try {
      const res  = await fetch(`${SERVER}/config/write`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ sensor, addr_h, addr_l, val_h, val_l }),
      });
      const data = await res.json();
      if (res.ok) {
        addLog(`[RAW] ✓ Write OK — ${data.message}`, "ok");
        onToast("Write executed successfully", "success");
      } else {
        addLog(`[RAW] ✗ Write Failed — ${data.error}`, "err");
        onToast(`Write failed: ${data.error}`, "error");
      }
    } catch {
      onToast("Network error", "error");
    }
    setRawLoading(false);
  }

  function logColor(type) {
    if (type === "ok")  return "var(--green)";
    if (type === "err") return "var(--red)";
    if (type === "inf") return "var(--amber)";
    if (type === "sys") return "var(--text-2)";
    return "var(--text)";
  }

  const paramRows = [
    { key: "sampling", label: "Sampling Period", opts: SP_LABEL },
    { key: "averaging", label: "Averaging",       opts: AV_LABEL },
    { key: "polarity",  label: "Output Polarity", opts: OP_LABEL },
    { key: "alarm",     label: "Alarm Mode",      opts: AL_LABEL },
  ];

  return (
    <div className="fade-up">

      {/* PAGE HEADER */}
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <div className="page-title">Sensor Configuration</div>
            <div className="page-sub">HARDWARE PARAMETERS · CD22 SERIES · SHARED ACROSS ALL SENSORS</div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-outline" onClick={loadConfig}>
              <Ic.Refresh /> Load from File
            </button>
            <button className="btn btn-blue" onClick={handleSave} disabled={saving}>
              {saving ? <><Spinner /> Saving…</> : <><Ic.Check /> Save to All Sensors</>}
            </button>
          </div>
        </div>
      </div>

      <div className="config-grid">

        {/* SHARED PARAMETERS CARD — single set of controls for all sensors */}
        <div className="card">
          <div className="card-header">
            <div className="card-title"><Ic.Sensor /> Sensor Parameters</div>
          </div>
          <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {paramRows.map(row => (
              <div key={row.key} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <label
                  style={{
                    minWidth: 150,
                    fontFamily: "var(--mono)",
                    fontSize: 13,
                    color: "var(--text-2)",
                  }}
                >
                  {row.label}
                </label>
                <select
                  className="form-select"
                  value={sharedConfig[row.key]}
                  onChange={e => updateShared(row.key, e.target.value)}
                  style={{ maxWidth: 200 }}
                >
                  {row.opts.map((o, i) => (
                    <option key={i} value={i}>{o}</option>
                  ))}
                </select>
              </div>
            ))}

            <div
              style={{
                borderTop: "1px solid var(--border)",
                paddingTop: 12,
                display: "flex",
                gap: 8,
                fontFamily: "var(--mono)",
                fontSize: 12,
                color: "var(--text-3)",
              }}
            >
              <Ic.Check />
              <span>
                These settings will be applied to sensors <strong>A</strong>, <strong>B</strong> and <strong>C</strong> simultaneously.
              </span>
            </div>
          </div>
        </div>

        {/* GLOBAL STREAM + RAW WRITE */}
        <div className="card">
          <div className="card-header">
            <div className="card-title"><Ic.Activity /> Global Stream Settings</div>
          </div>
          <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>

            <div>
              <label className="form-label">Stream Rate (Hz)</label>
              <select
                className="form-select"
                value={streamRate}
                onChange={e => setStreamRate(e.target.value)}
                style={{ maxWidth: 200 }}
              >
                {["0.5","1","2","5","10"].map(v => (
                  <option key={v} value={v}>{v} Hz</option>
                ))}
              </select>
              <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 6, fontFamily: "var(--mono)" }}>
                Current: {streamRate} Hz · {Math.round(1000 / parseFloat(streamRate))}ms interval
              </div>
              <button
                className="btn btn-outline btn-sm"
                style={{ marginTop: 8 }}
                onClick={applyStreamRate}
                disabled={busyStream}
              >
                {busyStream ? <><Spinner /> Applying…</> : "Apply Rate"}
              </button>
            </div>

            <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14 }}>
              <div className="section-title" style={{ marginBottom: 10 }}>Raw / Write Command</div>
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 4, fontFamily: "var(--mono)" }}>sensor</div>
                <select
                  className="form-select"
                  value={rawFields.sensor}
                  onChange={e => setRawFields(f => ({ ...f, sensor: e.target.value }))}
                  style={{ maxWidth: 120 }}
                >
                  {SENSOR_IDS.map(s => <option key={s}>{s}</option>)}
                </select>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 10 }}>
                {["addr_h","addr_l","val_h","val_l"].map(f => (
                  <div key={f}>
                    <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 4, fontFamily: "var(--mono)" }}>{f}</div>
                    <input
                      className="form-input"
                      placeholder="0x00"
                      value={rawFields[f]}
                      onChange={e => setRawFields(p => ({ ...p, [f]: e.target.value }))}
                      style={{ fontFamily: "var(--mono)" }}
                    />
                  </div>
                ))}
              </div>
              <button
                className="btn btn-outline btn-sm"
                style={{ fontFamily: "var(--mono)" }}
                onClick={handleExecuteWrite}
                disabled={rawLoading}
              >
                {rawLoading ? <><Spinner /> Executing…</> : <><Ic.Code /> Execute Write</>}
              </button>
            </div>

          </div>
        </div>

        {/* ACTIVITY LOG */}
        <div className="card">
          <div className="card-header">
            <div className="card-title"><Ic.Database /> Activity Log</div>
            <button className="btn btn-outline btn-sm" onClick={() => setLog([])}>
              Clear
            </button>
          </div>
          <div style={{
            padding:       "12px 16px",
            maxHeight:     220,
            overflowY:     "auto",
            display:       "flex",
            flexDirection: "column",
            gap:           4,
          }}>
            {log.length === 0 && (
              <span style={{ color: "var(--text-3)", fontFamily: "var(--mono)", fontSize: 12 }}>
                No activity yet
              </span>
            )}
            {log.map((l, i) => (
              <div key={i} style={{ display: "flex", gap: 12, fontFamily: "var(--mono)", fontSize: 12 }}>
                <span style={{ color: "var(--text-3)", flexShrink: 0 }}>{l.ts || "--:--:--"}</span>
                <span style={{ color: logColor(l.type) }}>{l.msg}</span>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}