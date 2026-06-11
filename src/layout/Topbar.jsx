import { Ic } from "../icons/Icons";
import { ROLE_COLOR } from "../constants/roles";
import { PAGE_LABELS } from "../constants/config";

export default function Topbar({ user, page, onLogout }) {
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