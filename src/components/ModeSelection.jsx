export default function ModeSelection({ onSelectMode }) {
  return (
    <div className="login-wrap">
      <div className="login-card" style={{ textAlign: "center" }}>
        <div className="login-header">
          <div className="login-logo-wrap" style={{ justifyContent: "center" }}>
            <div className="login-mark">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                style={{ color: "#fff" }}>
                <path d="M3 3v18h18" />
                <path d="M7 16l4-8 4 4 4-6" />
              </svg>
            </div>
            <span className="login-title">THICKNESS MONITORING</span>
          </div>
          <div className="login-sub">CD22 THICKNESS MONITORING SYSTEM</div>
        </div>

        <div style={{ margin: "8px 0 24px", color: "var(--text-2)", fontSize: 13, lineHeight: 1.6 }}>
          Select your sensor configuration to continue
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <button
            className="btn-primary"
            style={{
              background: "linear-gradient(135deg, var(--brand-mid), var(--brand-accent))",
              justifyContent: "center", padding: "14px 20px",
              fontSize: 14,
            }}
            onClick={() => onSelectMode("side-by-side")}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="2" width="20" height="20" rx="2.18"/>
              <line x1="2" y1="12" x2="22" y2="12"/>
              <line x1="12" y1="2" x2="12" y2="22"/>
            </svg>
            Side by Side Sensors
          </button>

          <button
            className="btn-primary"
            style={{
              background: "linear-gradient(135deg, #4a7a5e, #2d5a3f)",
              justifyContent: "center", padding: "14px 20px",
              fontSize: 14,
            }}
            onClick={() => onSelectMode("opposite")}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="7" width="8" height="10" rx="1"/>
              <rect x="14" y="7" width="8" height="10" rx="1"/>
              <path d="M6 17v3M18 17v3"/>
              <line x1="2" y1="12" x2="10" y2="12"/>
              <line x1="14" y1="12" x2="22" y2="12"/>
            </svg>
            Opposite Side Sensors
          </button>
        </div>

        <div style={{ marginTop: 24, fontSize: 11, color: "var(--text-3)" }}>
          Rajdeep Automation · CD22 Series
        </div>
      </div>
    </div>
  );
}