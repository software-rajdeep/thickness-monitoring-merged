import { useState, useEffect, useRef } from "react";
import { SERVER } from "../constants/config_opposite";

/* ── Mini toggle switch component ── */
function Toggle({ checked, onChange, label }) {
  return (
    <label className="toggle-row">
      <span className="toggle-label">{label}</span>
      <span
        className={`toggle-switch ${checked ? "on" : ""}`}
        onClick={(e) => { e.stopPropagation(); onChange(!checked); }}
      >
        <span className="toggle-knob" />
      </span>
    </label>
  );
}

/* ── Section header ── */
function Section({ title, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="email-section">
      <div className="email-section-header" onClick={() => setOpen(!open)}>
        <span className={`email-section-arrow ${open ? "open" : ""}`}>▶</span>
        <span className="email-section-title">{title}</span>
      </div>
      {open && <div className="email-section-body">{children}</div>}
    </div>
  );
}

export default function EmailAlertSettings({ onClose }) {
  const [config, setConfig] = useState(null);
  const [apiOptions, setApiOptions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testStatus, setTestStatus] = useState(null);
  const [testMsg, setTestMsg] = useState("");
  const [oauthStatus, setOauthStatus] = useState(null); // null | "loading" | "success" | "error"
  const [oauthMsg, setOauthMsg] = useState("");
  const panelRef = useRef(null);

  // ── Load config and API options ──
  useEffect(() => {
    async function load() {
      try {
        const [configRes, optionsRes] = await Promise.all([
          fetch(`${SERVER}/email-alerts/config`),
          fetch(`${SERVER}/email-alerts/api-options`),
        ]);
        if (configRes.ok) {
          const data = await configRes.json();
          setConfig(data);
        }
        if (optionsRes.ok) {
          const data = await optionsRes.json();
          setApiOptions(data);
        }
      } catch (err) {
        console.error("Failed to load email config:", err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  // ── Click outside to close ──
  useEffect(() => {
    function handleClick(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [onClose]);

  // ── Save config ──
  async function saveConfig(updated) {
    setSaving(true);
    try {
      const res = await fetch(`${SERVER}/email-alerts/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updated),
      });
      if (res.ok) {
        const configRes = await fetch(`${SERVER}/email-alerts/config`);
        if (configRes.ok) setConfig(await configRes.json());
      }
    } catch (err) {
      console.error("Failed to save config:", err);
    } finally {
      setSaving(false);
    }
  }

  // ── Update a field and save ──
  function updateAndSave(path, value) {
    const update = {};
    if (typeof path === "string") {
      update[path] = value;
    } else if (Array.isArray(path)) {
      let current = update;
      for (let i = 0; i < path.length - 1; i++) {
        current[path[i]] = {};
        current = current[path[i]];
      }
      current[path[path.length - 1]] = value;
    }
    saveConfig(update);
  }

  // ── Test email ──
  async function handleTest() {
    setTestStatus("sending");
    setTestMsg("");
    try {
      const res = await fetch(`${SERVER}/email-alerts/test`, {
        method: "POST",
      });
      const data = await res.json();
      if (res.ok) {
        setTestStatus("success");
        setTestMsg(data.message || "Test email sent!");
      } else {
        setTestStatus("error");
        setTestMsg(data.error || "Failed to send test email");
      }
    } catch {
      setTestStatus("error");
      setTestMsg("Network error");
    }
    setTimeout(() => setTestStatus(null), 4000);
  }

  // ── Google OAuth Sign-In ──
  async function handleGoogleSignIn() {
    setOauthStatus("loading");
    setOauthMsg("Opening Google sign-in...");
    try {
      const res = await fetch(`${SERVER}/email-alerts/auth-url`);
      const data = await res.json();
      if (res.ok && data.auth_url) {
        // Open the Google OAuth page in a new window
        const width = 500;
        const height = 600;
        const left = window.screenX + (window.outerWidth - width) / 2;
        const top = window.screenY + (window.outerHeight - height) / 2;
        const authWindow = window.open(
          data.auth_url,
          "google-oauth",
          `width=${width},height=${height},left=${left},top=${top}`
        );

        // Poll for the token file to appear (backend saves it on callback)
        setOauthMsg("Waiting for authentication...");
        let attempts = 0;
        const maxAttempts = 120; // 2 minutes
        const checkInterval = setInterval(async () => {
          attempts++;
          try {
            const configRes = await fetch(`${SERVER}/email-alerts/config`);
            if (configRes.ok) {
              const cfg = await configRes.json();
              if (cfg.gmail_authenticated) {
                clearInterval(checkInterval);
                setConfig(cfg);
                setOauthStatus("success");
                setOauthMsg("✓ Google authentication successful!");
                setTimeout(() => setOauthStatus(null), 4000);
                if (authWindow) authWindow.close();
              }
            }
          } catch {}
          if (attempts >= maxAttempts) {
            clearInterval(checkInterval);
            setOauthStatus("error");
            setOauthMsg("Authentication timed out. Please try again.");
            setTimeout(() => setOauthStatus(null), 4000);
          }
        }, 1000);
      } else {
        setOauthStatus("error");
        setOauthMsg(data.error || "Failed to get auth URL");
        setTimeout(() => setOauthStatus(null), 4000);
      }
    } catch (err) {
      setOauthStatus("error");
      setOauthMsg("Network error: " + err.message);
      setTimeout(() => setOauthStatus(null), 4000);
    }
  }

  // ── When API type changes ──
  function handleApiTypeChange(apiId) {
    const option = apiOptions.find(o => o.id === apiId);
    if (option && !option.is_oauth) {
      const updated = {
        api_type: apiId,
        smtp_config: {
          host: option.smtp_host || "",
          port: option.smtp_port || 587,
          use_tls: true,
        },
      };
      saveConfig(updated);
    } else if (option && option.is_oauth) {
      saveConfig({ api_type: apiId });
    }
  }

  if (loading) {
    return (
      <div className="email-panel" ref={panelRef}>
        <div className="email-panel-loading">Loading...</div>
      </div>
    );
  }

  if (!config) {
    return (
      <div className="email-panel" ref={panelRef}>
        <div className="email-panel-error">Failed to load configuration</div>
      </div>
    );
  }

  // ── Derive state from config ──
  const {
    enabled,
    api_type,
    smtp_config,
    recipient_email,
    alerts,
    cooldown,
    summary_report,
  } = config;

  return (
    <div className="email-panel" ref={panelRef}>
      {/* Header */}
      <div className="email-panel-header">
        <span className="email-panel-title">📧 Email Alert Settings</span>
        <button className="email-close-btn" onClick={onClose}>✕</button>
      </div>

      {/* Master Enable Toggle */}
      <div className="email-master-toggle">
        <span className="email-master-label">Enable Email Alerts</span>
        <span
          className={`toggle-switch large ${enabled ? "on" : ""}`}
          onClick={() => updateAndSave("enabled", !enabled)}
        >
          <span className="toggle-knob" />
        </span>
      </div>

      {enabled && (
        <div className="email-panel-content">
          {/* ── API Selection ── */}
          <Section title="1. API Selection" defaultOpen={true}>
            <p className="email-hint">Choose your email provider:</p>
            <div className="api-options-grid">
              {apiOptions.map(opt => (
                <div
                  key={opt.id}
                  className={`api-option-card ${api_type === opt.id ? "selected" : ""}`}
                  onClick={() => handleApiTypeChange(opt.id)}
                >
                  <div className="api-option-name">{opt.name}</div>
                  <div className="api-option-desc">{opt.description}</div>
                  {opt.requires_app_password && (
                    <div className="api-option-note">Requires App Password</div>
                  )}
                  {opt.is_oauth && opt.authenticated && (
                    <div className="api-option-auth">✓ Authenticated</div>
                  )}
                </div>
              ))}
            </div>

            {/* Google OAuth Sign-In Button (only when Gmail OAuth is selected) */}
            {api_type === "gmail_oauth" && (
              <div className="oauth-section">
                {!config.gmail_authenticated ? (
                  <div>
                    <p className="email-hint">
                      Click below to sign in with Google. No password needed — 
                      just grant the app permission to send emails on your behalf.
                    </p>
                    <button
                      className="email-btn email-btn-google"
                      onClick={handleGoogleSignIn}
                      disabled={oauthStatus === "loading"}
                    >
                      <svg width="18" height="18" viewBox="0 0 48 48">
                        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
                        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.41-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
                        <path fill="#FBBC05" d="M10.53 28.59A14.5 14.5 0 0 1 9.5 24c0-1.59.28-3.14.76-4.59l-7.98-6.19A23.99 23.99 0 0 0 0 24c0 3.8.89 7.4 2.56 10.78l7.97-6.19z"/>
                        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
                      </svg>
                      {oauthStatus === "loading" ? "Connecting..." : "Sign in with Google"}
                    </button>
                    {oauthStatus && (
                      <span className={`oauth-status ${oauthStatus}`}>{oauthMsg}</span>
                    )}
                  </div>
                ) : (
                  <div className="oauth-authenticated">
                    <span className="oauth-badge">✓ Gmail Authenticated</span>
                    <p className="email-hint">Your Google account is connected. Emails will be sent via Gmail API.</p>
                  </div>
                )}
              </div>
            )}
          </Section>

          {/* ── SMTP Configuration ── */}
          <Section title="2. SMTP Configuration" defaultOpen={true}>
            <div className="email-form-row">
              <label className="email-label">SMTP Host</label>
              <input
                className="email-input"
                type="text"
                value={smtp_config.host}
                onChange={e => saveConfig({
                  smtp_config: { ...smtp_config, host: e.target.value }
                })}
              />
            </div>
            <div className="email-form-row">
              <label className="email-label">SMTP Port</label>
              <input
                className="email-input"
                type="number"
                value={smtp_config.port}
                onChange={e => saveConfig({
                  smtp_config: { ...smtp_config, port: parseInt(e.target.value) || 587 }
                })}
              />
            </div>
            <div className="email-form-row">
              <label className="email-label">Email Address</label>
              <input
                className="email-input"
                type="email"
                placeholder="your-email@gmail.com"
                value={smtp_config.email}
                onChange={e => saveConfig({
                  smtp_config: { ...smtp_config, email: e.target.value }
                })}
              />
            </div>
            <div className="email-form-row">
              <label className="email-label">App Password</label>
              <input
                className="email-input"
                type="password"
                placeholder={smtp_config.password ? "********" : "Enter password"}
                value={smtp_config.password === "********" ? "" : smtp_config.password}
                onChange={e => saveConfig({
                  smtp_config: { ...smtp_config, password: e.target.value }
                })}
              />
            </div>
            <div className="email-form-row">
              <label className="email-label">Use TLS</label>
              <Toggle
                checked={smtp_config.use_tls}
                onChange={v => saveConfig({
                  smtp_config: { ...smtp_config, use_tls: v }
                })}
              />
            </div>
          </Section>

          {/* ── Recipient ── */}
          <Section title="3. Recipient Settings" defaultOpen={true}>
            <div className="email-form-row">
              <label className="email-label">Recipient Email</label>
              <input
                className="email-input"
                type="email"
                placeholder="admin@company.com"
                value={recipient_email}
                onChange={e => updateAndSave("recipient_email", e.target.value)}
              />
            </div>
          </Section>

          {/* ── Threshold Alerts ── */}
          <Section title="4. Threshold Alerts" defaultOpen={true}>
            <Toggle
              label="Below MIN limit"
              checked={alerts.threshold_below_min}
              onChange={v => saveConfig({ alerts: { ...alerts, threshold_below_min: v } })}
            />
            <Toggle
              label="Above MAX limit"
              checked={alerts.threshold_above_max}
              onChange={v => saveConfig({ alerts: { ...alerts, threshold_above_max: v } })}
            />
            <Toggle
              label="Out of tolerance range"
              checked={alerts.threshold_out_of_tolerance}
              onChange={v => saveConfig({ alerts: { ...alerts, threshold_out_of_tolerance: v } })}
            />
          </Section>

          {/* ── Sensor Alerts ── */}
          <Section title="5. Sensor Alerts" defaultOpen={false}>
            <Toggle
              label="Sensor disconnected/lost connection"
              checked={alerts.sensor_disconnected}
              onChange={v => saveConfig({ alerts: { ...alerts, sensor_disconnected: v } })}
            />
          </Section>

          {/* ── Session / Run Alerts ── */}
          <Section title="6. Session / Run Alerts" defaultOpen={false}>
            <Toggle
              label="Run Session starts"
              checked={alerts.run_session_start}
              onChange={v => saveConfig({ alerts: { ...alerts, run_session_start: v } })}
            />
            <Toggle
              label="Run Session ends (with summary)"
              checked={alerts.run_session_end}
              onChange={v => saveConfig({ alerts: { ...alerts, run_session_end: v } })}
            />
          </Section>

          {/* ── Cooldown / Spam Prevention ── */}
          <Section title="7. Cooldown / Spam Prevention" defaultOpen={false}>
            <Toggle
              label="Enable cooldown"
              checked={cooldown.enabled}
              onChange={v => saveConfig({ cooldown: { ...cooldown, enabled: v } })}
            />
            <div className="email-form-row">
              <label className="email-label">Cooldown period (minutes)</label>
              <input
                className="email-input"
                type="number"
                min="1"
                max="60"
                value={cooldown.minutes}
                onChange={e => saveConfig({
                  cooldown: { ...cooldown, minutes: parseInt(e.target.value) || 5 }
                })}
              />
            </div>
            <p className="email-hint">Grouped alerts are sent together when multiple events occur during cooldown.</p>
          </Section>

          {/* ── Summary Reports ── */}
          <Section title="8. Daily / Weekly Summary Reports" defaultOpen={false}>
            <Toggle
              label="Enable automated summary reports"
              checked={summary_report.enabled}
              onChange={v => saveConfig({ summary_report: { ...summary_report, enabled: v } })}
            />
            {summary_report.enabled && (
              <div className="email-form-row">
                <label className="email-label">Frequency</label>
                <select
                  className="email-input"
                  value={summary_report.frequency}
                  onChange={e => saveConfig({
                    summary_report: { ...summary_report, frequency: e.target.value }
                  })}
                >
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                </select>
              </div>
            )}
            {summary_report.enabled && (
              <p className="email-hint">
                Report includes: Min/Max/Avg thickness, total readings, and breach counts.
              </p>
            )}
          </Section>

          {/* ── Test & Save ── */}
          <div className="email-actions">
            <button
              className="email-btn email-btn-test"
              onClick={handleTest}
              disabled={testStatus === "sending" || !recipient_email}
            >
              {testStatus === "sending" ? "Sending..." : "Send Test Email"}
            </button>
            {testStatus === "success" && (
              <span className="email-test-success">✓ {testMsg}</span>
            )}
            {testStatus === "error" && (
              <span className="email-test-error">✕ {testMsg}</span>
            )}
            {saving && <span className="email-saving">Saving...</span>}
          </div>
        </div>
      )}
    </div>
  );
}