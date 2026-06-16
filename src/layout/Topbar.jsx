import { useState } from "react";
import { Ic } from "../icons/Icons";
import { ROLE_COLOR } from "../constants/roles";
import { PAGE_LABELS } from "../constants/config";
import EmailAlertSettings from "../components/EmailAlertSettings";

export default function Topbar({ user, page, onLogout }) {
  const [showEmailSettings, setShowEmailSettings] = useState(false);

  const isSuperAdmin = user?.role === "superadmin";

  return (
    <div className="topbar">

      {/* BRAND */}
      <div className="topbar-brand">
        <div className="brand-mark">
          <Ic.Logo />
        </div>
        <div className="brand-name">
          Thickness<span>Monitoring</span>
        </div>
      </div>

      <div className="topbar-divider" />

      {/* BREADCRUMB */}
      <div className="topbar-breadcrumb">
        <span>cd22</span>
        <span className="sep">/</span>
        <span className="active">{PAGE_LABELS[page] || page}</span>
      </div>

      {/* RIGHT */}
      <div className="topbar-right">
        {/* Super Admin Email Alert Toggle Button */}
        {isSuperAdmin && (
          <>
            <button
              className={`email-alert-toggle-btn ${showEmailSettings ? "active" : ""}`}
              onClick={() => setShowEmailSettings(!showEmailSettings)}
              title="Email Alert Settings"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
                <polyline points="22,6 12,13 2,6"/>
              </svg>
              <span className="email-btn-label">Email Alerts</span>
            </button>
            {showEmailSettings && (
              <EmailAlertSettings onClose={() => setShowEmailSettings(false)} />
            )}
          </>
        )}
        <span className={`role-badge ${ROLE_COLOR[user.role]}`}>
          {user.role}
        </span>
        <div className="user-btn" onClick={onLogout} title="Logout">
          <div className="avatar">
            {user.username[0].toUpperCase()}
          </div>
          <span className="user-name">{user.username}</span>
        </div>
      </div>

    </div>
  );
}
