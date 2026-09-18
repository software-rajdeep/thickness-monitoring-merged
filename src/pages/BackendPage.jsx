import { useState, useEffect } from "react";
import { Ic } from "../icons/Icons";
import { ROLE_ACCESS, ROLE_COLOR } from "../constants/roles";
import AccessDenied from "../components/AccessDenied";
import Spinner from "../components/Spinner";
import { authHeaders } from "../constants/auth";
import { SERVER } from "../constants/config";

export default function BackendPage({ user, sensorMode }) {
  if (!ROLE_ACCESS[user.role]?.includes("backend")) return <AccessDenied />;

  const canManageUsers = user.role === "superadmin" || user.role === "admin";

  // Reference-mode sensor setup only applies to "side by side with reference".
  // Other modes open the plain (measurement-only) setup page unchanged.
  const setupUrl = sensorMode === "sbs-reference"
    ? "/sensor_setup.html?mode=reference"
    : "/sensor_setup.html";

  const [users,      setUsers]      = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [dbStatus,   setDbStatus]   = useState(null);
  const [srvConfig,  setSrvConfig]  = useState(null);
  const [newUser,    setNewUser]    = useState({ username: "", password: "", role: "worker" });
  const [adding,     setAdding]     = useState(false);
  const [deleting,   setDeleting]   = useState(null);
  const [toast,      setToast]      = useState(null);

  function showToast(msg, type = "success") {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }

  async function fetchUsers() {
    try {
      const res  = await fetch(`${SERVER}/auth/users`, { headers: authHeaders() });
      const data = await res.json();
      setUsers(data);
    } catch {
      showToast("Failed to load users", "error");
    }
    setLoading(false);
  }

  async function fetchDbStatus() {
    try {
      const res  = await fetch(`${SERVER}/db/status`, { headers: authHeaders() });
      const data = await res.json();
      setDbStatus(data);
    } catch {
      setDbStatus(null);
    }
  }

  async function fetchServerConfig() {
    try {
      const res  = await fetch(`${SERVER}/server/config`);
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
    }, 5000);
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
      const res  = await fetch(`${SERVER}/auth/users`, {
        method:  "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body:    JSON.stringify({ username, password, role }),
      });
      const data = await res.json();
      if (res.ok) {
        showToast(`User '${username}' added successfully`, "success");
        setNewUser({ username: "", password: "", role: "worker" });
        fetchUsers();
        fetchDbStatus();
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
      const res  = await fetch(`${SERVER}/auth/users/${id}`, { method: "DELETE", headers: authHeaders() });
      const data = await res.json();
      if (res.ok) {
        showToast(`User '${username}' deleted`, "success");
        fetchUsers();
        fetchDbStatus();
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
                <td className="td-mono">sensor_filtered_readings</td>
                <td className="td-mono">
                  {dbStatus ? dbStatus.filtered.toLocaleString() : "-"}
                </td>
                <td className="td-mono">
                  {srvConfig ? srvConfig.limit_filtered.toLocaleString() : "10,000,000"}
                </td>
                <td>
                  <span className={`badge ${dbStatus && srvConfig && dbStatus.filtered > srvConfig.limit_filtered * 0.8 ? "badge-amber" : "badge-green"}`}>
                    {dbStatus && srvConfig && dbStatus.filtered > srvConfig.limit_filtered * 0.8 ? "Almost Full" : "Healthy"}
                  </span>
                </td>
              </tr>
              <tr>
                <td className="td-mono">sensor_unfiltered_readings</td>
                <td className="td-mono">
                  {dbStatus ? dbStatus.unfiltered.toLocaleString() : "-"}
                </td>
                <td className="td-mono">
                  {srvConfig ? srvConfig.limit_unfiltered.toLocaleString() : "1,000,000"}
                </td>
                <td>
                  <span className={`badge ${dbStatus && srvConfig && dbStatus.unfiltered > srvConfig.limit_unfiltered * 0.8 ? "badge-amber" : "badge-green"}`}>
                    {dbStatus && srvConfig && dbStatus.unfiltered > srvConfig.limit_unfiltered * 0.8 ? "Almost Full" : "Healthy"}
                  </span>
                </td>
              </tr>
              <tr>
                <td className="td-mono">users</td>
                <td className="td-mono">
                  {dbStatus ? dbStatus.users : "-"}
                </td>
                <td className="td-mono">-</td>
                <td><span className="badge badge-green">Healthy</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* SENSOR NETWORK SETUP */}
      {canManageUsers && (
        <div className="section">
          <div className="section-header">
            <span className="section-title">Sensor Network Setup</span>
          </div>
          <div style={{
            background:   "var(--bg2)",
            border:       "1px solid var(--border)",
            borderRadius: "var(--r2)",
            padding:      "16px 18px",
            display:      "flex",
            alignItems:   "center",
            justifyContent: "space-between",
            gap: 12,
            flexWrap: "wrap",
          }}>
            <button
              className="btn btn-blue"
              onClick={() => window.open(setupUrl, "_blank")}
            >
              <Ic.Wifi /> Open Sensor Setup
            </button>
          </div>
        </div>
      )}

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