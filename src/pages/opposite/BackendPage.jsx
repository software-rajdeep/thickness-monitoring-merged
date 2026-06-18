import { useState, useEffect } from "react";
import { Ic } from "../../icons/Icons";
import { ROLE_ACCESS, ROLE_COLOR } from "../../constants/roles";
import AccessDenied from "../../components/AccessDenied";
import Spinner from "../../components/Spinner";
import { SERVER, DEFAULT_SERVER, setServerBase } from "../../constants/config_opposite";

export default function BackendPage({ user }) {
  if (!ROLE_ACCESS[user.role]?.includes("backend")) return <AccessDenied />;

  const canManageUsers = user.role === "superadmin" || user.role === "admin";

  const [users,      setUsers]      = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [dbStatus,   setDbStatus]   = useState(null);
  const [srvConfig,  setSrvConfig]  = useState(null);
  const [newUser,    setNewUser]    = useState({ username: "", password: "", role: "worker" });
  const [adding,     setAdding]     = useState(false);
  const [deleting,   setDeleting]   = useState(null);
  const [toast,      setToast]      = useState(null);
  const [apiBase,    setApiBase]    = useState(() => SERVER);

  function showToast(msg, type = "success") {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }

  function handleApiSave() {
    const next = apiBase.trim();
    if (!next) {
      showToast("Server URL is required", "error");
      return;
    }
    setServerBase(next);
    showToast("Server updated. Reloading…", "success");
    setTimeout(() => window.location.reload(), 600);
  }

  function handleApiReset() {
    setApiBase(DEFAULT_SERVER);
    setServerBase(DEFAULT_SERVER);
    showToast("Server reset to default. Reloading…", "success");
    setTimeout(() => window.location.reload(), 600);
  }

  async function fetchUsers() {
    try {
      const res  = await fetch(`${SERVER}/users`);
      const data = await res.json();
      setUsers(data);
    } catch {
      showToast("Failed to load users", "error");
    }
    setLoading(false);
  }

  async function fetchDbStatus() {
    try {
      const res  = await fetch(`${SERVER}/db/status`);
      const data = await res.json();
      setDbStatus(data);
    } catch {
      setDbStatus(null);
    }
  }

  async function fetchServerConfig() {
    try {
      const res  = await fetch(`${SERVER}/server/config?mode=opposite`);
      const data = await res.json();
      setSrvConfig(data);
    } catch {
      setSrvConfig(null);
    }
  }

  useEffect(() => {
    fetchUsers();
    fetchDbStatus();
    fetchServerConfig();
    const iv = setInterval(() => {
      fetchDbStatus();
      fetchServerConfig();
    }, 10000);
    return () => clearInterval(iv);
  }, []);

  async function handleAdd() {
    const { username, password, role } = newUser;
    if (!username || !password) {
      showToast("Username and password required", "error");
      return;
    }
    setAdding(true);
    try {
      const res  = await fetch(`${SERVER}/users`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ username, password, role }),
      });
      const data = await res.json();
      if (res.ok) {
        showToast(`User '${username}' added successfully`, "success");
        setNewUser({ username: "", password: "", role: "worker" });
        fetchUsers();
      } else {
        showToast(data.error || "Failed to add user", "error");
      }
    } catch {
      showToast("Network error", "error");
    }
    setAdding(false);
  }

  async function handleDelete(id, username) {
    if (username === user.username) {
      showToast("Cannot delete your own account", "error");
      return;
    }
    setDeleting(id);
    try {
      const res  = await fetch(`${SERVER}/users/${id}`, { method: "DELETE" });
      const data = await res.json();
      if (res.ok) {
        showToast(`User '${username}' deleted`, "success");
        fetchUsers();
      } else {
        showToast(data.error || "Failed to delete user", "error");
      }
    } catch {
      showToast("Network error", "error");
    }
    setDeleting(null);
  }

  return (
    <div className="fade-up">

      {/* PAGE HEADER */}
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <div className="page-title">Backend Access</div>
            <div className="page-sub">SYSTEM ADMINISTRATION</div>
          </div>
          <span className={`role-badge ${ROLE_COLOR[user.role]}`}>
            {user.role}
          </span>
        </div>
      </div>

      {/* USER MANAGEMENT */}
      {canManageUsers && (
        <>
          {/* ADD USER */}
          <div className="section">
            <div className="section-header">
              <span className="section-title">Add New User</span>
            </div>
            <div style={{
              background:   "var(--bg2)",
              border:       "1px solid var(--border)",
              borderRadius: "var(--r2)",
              padding:      "18px",
              display:      "flex",
              gap:          12,
              flexWrap:     "wrap",
              alignItems:   "flex-end",
            }}>
              <div style={{ flex: 1, minWidth: 140 }}>
                <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 5, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.8px" }}>
                  Username
                </div>
                <input
                  className="form-input"
                  placeholder="Enter username"
                  value={newUser.username}
                  onChange={e => setNewUser(p => ({ ...p, username: e.target.value }))}
                />
              </div>
              <div style={{ flex: 1, minWidth: 140 }}>
                <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 5, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.8px" }}>
                  Password
                </div>
                <input
                  className="form-input"
                  type="password"
                  placeholder="Enter password"
                  value={newUser.password}
                  onChange={e => setNewUser(p => ({ ...p, password: e.target.value }))}
                />
              </div>
              <div style={{ minWidth: 140 }}>
                <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 5, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.8px" }}>
                  Role
                </div>
                <select
                  className="form-select"
                  value={newUser.role}
                  onChange={e => setNewUser(p => ({ ...p, role: e.target.value }))}
                >
                  {user.role === "superadmin" && <option value="superadmin">Superadmin</option>}
                  <option value="admin">Admin</option>
                  <option value="supervisor">Supervisor</option>
                  <option value="worker">Worker</option>
                </select>
              </div>
              <button
                className="btn btn-blue"
                onClick={handleAdd}
                disabled={adding}
              >
                {adding ? <><Spinner /> Adding…</> : <><Ic.Check /> Add User</>}
              </button>
            </div>
          </div>

          {/* USER LIST */}
          <div className="section">
            <div className="section-header">
              <span className="section-title">User Management</span>
              <button className="btn btn-outline btn-sm" onClick={fetchUsers}>
                <Ic.Refresh /> Refresh
              </button>
            </div>
            <div className="table-wrap">
              {loading ? (
                <div style={{ padding: "32px", textAlign: "center", color: "var(--text-3)", fontFamily: "var(--mono)", fontSize: 13 }}>
                  Loading users…
                </div>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Username</th>
                      <th>Role</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map(u => (
                      <tr key={u.id}>
                        <td className="td-mono td-dim">{u.id}</td>
                        <td className="td-mono">{u.username}</td>
                        <td>
                          <span className={`role-badge ${ROLE_COLOR[u.role] || "worker"}`}>
                            {u.role}
                          </span>
                        </td>
                        <td>
                          {u.username === user.username ? (
                            <span style={{ fontSize: 11, color: "var(--text-3)", fontFamily: "var(--mono)" }}>
                              (current user)
                            </span>
                          ) : (
                            <button
                              className="btn btn-red btn-sm"
                              onClick={() => handleDelete(u.id, u.username)}
                              disabled={deleting === u.id}
                            >
                              {deleting === u.id ? <Spinner /> : <Ic.X />}
                              Delete
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}

      {/* DATABASE STATUS */}
      <div className="section">
        <div className="section-header">
          <span className="section-title">Database Status</span>
          <button className="btn btn-outline btn-sm" onClick={fetchDbStatus}>
            <Ic.Refresh /> Refresh
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Table</th>
                <th>Total Rows</th>
                <th>Limit</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="td-mono">opposite_thickness_readings</td>
                <td className="td-mono">
                  {dbStatus ? dbStatus.thickness.toLocaleString() : "—"}
                </td>
                <td className="td-mono">
                  {srvConfig ? srvConfig.limit_thickness.toLocaleString() : "10,000,000"}
                </td>
                <td>
                  <span className={`badge ${dbStatus && srvConfig && dbStatus.thickness > srvConfig.limit_thickness * 0.8 ? "badge-amber" : "badge-green"}`}>
                    {dbStatus && srvConfig && dbStatus.thickness > srvConfig.limit_thickness * 0.8 ? "Almost Full" : "Healthy"}
                  </span>
                </td>
              </tr>
              <tr>
                <td className="td-mono">opposite_thickness_raw_readings</td>
                <td className="td-mono">
                  {dbStatus ? dbStatus.thickness_raw.toLocaleString() : "—"}
                </td>
                <td className="td-mono">
                  {srvConfig ? srvConfig.limit_thickness_raw.toLocaleString() : "1,000,000"}
                </td>
                <td>
                  <span className={`badge ${dbStatus && srvConfig && dbStatus.thickness_raw > srvConfig.limit_thickness_raw * 0.8 ? "badge-amber" : "badge-green"}`}>
                    {dbStatus && srvConfig && dbStatus.thickness_raw > srvConfig.limit_thickness_raw * 0.8 ? "Almost Full" : "Healthy"}
                  </span>
                </td>
              </tr>
              <tr>
                <td className="td-mono">users</td>
                <td className="td-mono">
                  {dbStatus ? dbStatus.users : "—"}
                </td>
                <td className="td-mono">—</td>
                <td><span className="badge badge-green">Healthy</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* API CONNECTION */}
      <div className="section">
        <div className="section-header">
          <span className="section-title">API Connection</span>
        </div>
        <div style={{
          background: "var(--bg2)",
          border: "1px solid var(--border)",
          borderRadius: "var(--r2)",
          padding: "16px 18px",
          display: "flex",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "flex-end",
        }}>
          <div style={{ flex: 1, minWidth: 220 }}>
            <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 5, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.8px" }}>
              Backend URL
            </div>
            <input
              className="form-input"
              value={apiBase}
              onChange={e => setApiBase(e.target.value)}
              placeholder={DEFAULT_SERVER}
            />
            <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 6, fontFamily: "var(--mono)" }}>
              Example: http://192.168.1.2:5000
            </div>
          </div>
          <button className="btn btn-blue" onClick={handleApiSave}>
            <Ic.Check /> Save URL
          </button>
          <button className="btn btn-outline" onClick={handleApiReset}>
            Reset
          </button>
        </div>
      </div>

      {/* SERVER CONFIGURATION */}
      <div className="section">
        <div className="section-header">
          <span className="section-title">Server Configuration</span>
          <button className="btn btn-outline btn-sm" onClick={fetchServerConfig}>
            <Ic.Refresh /> Refresh
          </button>
        </div>
        <div className="code-block">
          {srvConfig ? (
            <pre style={{ margin: 0 }}>{`SENSOR_CONFIGS = {
${Object.entries(srvConfig.sensor_configs).map(([k, v]) =>
  `  "${k}": {"ip": "${v.ip}", "port": ${v.port}}`
).join(',\n')}
}

SERVER_PORT      = ${srvConfig.server_port}
SENSOR_TIMEOUT   = ${srvConfig.sensor_timeout}
LIMIT_THICKNESS     = ${srvConfig.limit_thickness.toLocaleString()}
LIMIT_THICKNESS_RAW = ${srvConfig.limit_thickness_raw.toLocaleString()}
DB_HOST             = ${srvConfig.db_host}
DB_NAME             = ${srvConfig.db_name}`}
            </pre>
          ) : (
            <span style={{ color: "var(--text-3)", fontFamily: "var(--mono)", fontSize: 12 }}>
              Loading server configuration…
            </span>
          )}
        </div>
      </div>

      {/* TOAST */}
      {toast && (
        <div className={`toast ${toast.type}`}>
          {toast.type === "success"
            ? <span style={{ color: "var(--green)" }}><Ic.Check /></span>
            : <span style={{ color: "var(--red)" }}><Ic.X /></span>
          }
          {toast.msg}
        </div>
      )}

    </div>
  );
}