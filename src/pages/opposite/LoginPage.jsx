import { useState } from "react";
import { Ic } from "../../icons/Icons";
import Spinner from "../../components/Spinner";
import { SERVER } from "../../constants/config_opposite";
import { login } from "../../constants/auth";

export default function LoginPage({ onLogin }) {
  const [company,     setCompany]     = useState("");
  const [u,           setU]           = useState("");
  const [p,           setP]           = useState("");
  const [err,         setErr]         = useState("");
  const [loading,     setLoading]     = useState(false);
  const [showPwd,     setShowPwd]     = useState(false);

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
      const usr = await login(company, u, p);
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
          <div className="login-sub">CD22 OPPOSITE SENSORS SYSTEM</div>
        </div>

        {/* ERROR */}
        {err && (
          <div className="error-box">
            <Ic.AlertTriangle />
            {err}
          </div>
        )}

        {/* COMPANY */}
        <div className="form-field">
          <label className="form-label">Company</label>
          <div className="input-wrap">
            <Ic.User />
            <input
              value={company}
              onChange={e => setCompany(e.target.value)}
              placeholder="Your company name"
              onKeyDown={e => e.key === "Enter" && handleLogin()}
              autoFocus
            />
          </div>
        </div>

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
            />
            <button
              type="button"
              className="pwd-toggle"
              onClick={() => setShowPwd(v => !v)}
              tabIndex={-1}
              aria-label={showPwd ? "Hide password" : "Show password"}
            >
              {showPwd ? <Ic.EyeOff /> : <Ic.Eye />}
            </button>
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

        {/* HELP */}
        <div style={{
          marginTop: 20,
          borderTop: "1px solid var(--border)",
          paddingTop: 16,
          textAlign: "center",
        }}>
          <div style={{
            fontSize: 12,
            color: "var(--text-2)",
            marginBottom: 4,
          }}>
            Need Help?
          </div>
          <a
            href="mailto:support@rajdeep.in"
            style={{
              fontSize: 13,
              color: "var(--blue)",
              fontWeight: 500,
              textDecoration: "none",
            }}
          >
            contact your administrator
          </a>
        </div>

      </div>
    </div>
  );
}