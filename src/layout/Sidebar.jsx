import { useState } from "react";
import { Ic } from "../icons/Icons";
import { ROLE_ACCESS } from "../constants/roles";
import { NAV_ITEMS } from "../constants/config";

const ICON_MAP = {
  Dashboard: <Ic.Dashboard />,
  Sensor:    <Ic.Sensor />,
  Activity:  <Ic.Activity />,
  Download:  <Ic.Download />,
  Backend:   <Ic.Backend />,
};

function NavContent({ user, page, onNavigate, onLogout, onClose }) {
  const access     = ROLE_ACCESS[user.role] || [];
  const visibleNav = NAV_ITEMS.filter(n => access.includes(n.id));
  const mainItems  = visibleNav.filter(n => n.section === "main");
  const adminItems = visibleNav.filter(n => n.section === "admin");

  function handleNav(id) {
    onNavigate(id);
    if (onClose) onClose();
  }

  function handleLogout() {
    onLogout();
    if (onClose) onClose();
  }

  return (
    <>
      <div className="nav-section-label">Navigation</div>

      {mainItems.map(n => (
        <div
          key={n.id}
          className={`nav-item ${page === n.id ? "active" : ""}`}
          onClick={() => handleNav(n.id)}
        >
          {ICON_MAP[n.icon]}
          {n.label}
        </div>
      ))}

      {adminItems.length > 0 && (
        <>
          <div className="nav-separator" />
          <div className="nav-section-label">Administration</div>
          {adminItems.map(n => (
            <div
              key={n.id}
              className={`nav-item ${page === n.id ? "active" : ""}`}
              onClick={() => handleNav(n.id)}
            >
              {ICON_MAP[n.icon]}
              {n.label}
            </div>
          ))}
        </>
      )}

      <div className="nav-spacer" />
      <div className="nav-separator" />

      <div className="nav-item danger" onClick={handleLogout}>
        <Ic.Logout />
        Logout
      </div>
    </>
  );
}

export default function Sidebar({ user, page, onNavigate, onLogout }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* ── DESKTOP SIDEBAR ── */}
      <div className="sidebar">
        <NavContent
          user={user}
          page={page}
          onNavigate={onNavigate}
          onLogout={onLogout}
        />
      </div>

      {/* ── MOBILE FLOATING BUTTON ── */}
      <button
        className="mobile-nav-btn"
        onClick={() => setOpen(true)}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2.5">
          <line x1="3" y1="6" x2="21" y2="6"/>
          <line x1="3" y1="12" x2="21" y2="12"/>
          <line x1="3" y1="18" x2="21" y2="18"/>
        </svg>
      </button>

      {/* ── MOBILE OVERLAY ── */}
      <div
        className={`mobile-overlay ${open ? "open" : ""}`}
        onClick={() => setOpen(false)}
      />

      {/* ── MOBILE DRAWER ── */}
      <div className={`mobile-drawer ${open ? "open" : ""}`}>
        <div style={{
          display:        "flex",
          alignItems:     "center",
          justifyContent: "space-between",
          marginBottom:   12,
          paddingBottom:  12,
          borderBottom:   "1px solid var(--border)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div className="brand-mark"><Ic.Logo /></div>
            <div className="brand-name">Thickness<span>Mon</span></div>
          </div>
          <button
            onClick={() => setOpen(false)}
            style={{
              background: "none",
              border:     "none",
              color:      "var(--text-2)",
              cursor:     "pointer",
              padding:    4,
            }}
          >
            <Ic.X />
          </button>
        </div>

        <NavContent
          user={user}
          page={page}
          onNavigate={onNavigate}
          onLogout={onLogout}
          onClose={() => setOpen(false)}
        />
      </div>
    </>
  );
}