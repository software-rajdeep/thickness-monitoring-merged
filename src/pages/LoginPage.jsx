import { useState } from "react";
import { Ic } from "../icons/Icons";
import Spinner from "../components/Spinner";
import { SERVER } from "../constants/config";
import { login } from "../constants/auth";

export default function LoginPage({ onLogin }) {
  const [u,           setU]       = useState("");
  const [p,           setP]       = useState("");
  const [err,         setErr]     = useState("");
  const [loading,     setLoading] = useState(false);
  const [showPwd,     setShowPwd] = useState(false);

  function fetchWithTimeout(url, options = {}, timeoutMs = 8000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    return fetch(url, { ...options, signal: controller.signal })
      .finally(() => clearTimeout(timeoutId));
  }

  async function handleLogin() {
    if (!u || !p) { setErr("Please fill in both fields."); return; }
    setLoading(true);
    setErr("");

    try {
      const usr = await login(u, p);
      onLogin({
        username: usr.username,
        role: usr.role,
        customer_id: usr.customer_id,
        customer_name: usr.customer_name,
        email: usr.email,
      });
    } catch (e) {
      setErr(e.message || "Invalid username or password.");
    }

    setLoading(false);
  }

  return (
    <div className="login-wrap">
      <div className="login-card">

        {/* HEADER */}
        <div className="login-header">
          <div className="login-logo-wrap">
            <div className="login-mark"><Ic.Logo /></div>
            <span className="login-title">THICKNESSMONITORING</span>
          </div>
          <div className="login-sub">CD22 THICKNESS MONITORING SYSTEM</div>
        </div>

        {/* ERROR */}
        {err && (
          <div className="error-box">
            <Ic.AlertTriangle />
            {err}
          </div>
        )}

        {/* USERNAME */}
        <div className="form-field">
          <label className="form-label">Username</label>
          <div className="input-wrap">
            <Ic.User />
            <input
              value={u}
              onChange={e => setU(e.target.value)}
              placeholder="Enter username"
              onKeyDown={e => e.key === "Enter" && handleLogin()}
              autoFocus
            />
          </div>
        </div>

        {/* PASSWORD */}
        <div className="form-field">
          <label className="form-label">Password</label>
          <div className="input-wrap">
            <Ic.Lock />
            <input
              type={showPwd ? "text" : "password"}
              value={p}
              onChange={e => setP(e.target.value)}
              placeholder="Enter password"
              onKeyDown={e => e.key === "Enter" && handleLogin()}
              style={{ flex: 1 }}
            />
            <span
              onClick={() => setShowPwd(s => !s)}
              style={{
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                color: "var(--text-3)",
                paddingLeft: 4,
              }}
            >
              {showPwd ? <Ic.EyeOff /> : <Ic.Eye />}
            </span>
          </div>
        </div>

        {/* SUBMIT */}
        <button
          className="btn-primary"
          onClick={handleLogin}
          disabled={loading}
        >
          {loading ? <><Spinner /> Authenticating…</> : "Sign In"}
        </button>

        {/* HELP SECTION */}
        <div style={{
          marginTop:12,
          textAlign:"center",
        }}>
          <a
            href="mailto:support@rajdeep.in"
            style={{
              fontSize:12,
              color:"var(--text-3)",
              textDecoration:"none",
            }}
          >
            Need help? contact your administrator
          </a>
        </div>
      </div>
    </div>
  );
}