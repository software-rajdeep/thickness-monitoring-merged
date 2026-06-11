import { Ic } from "../icons/Icons";

export default function AccessDenied() {
  return (
    <div className="access-denied fade-up">
      <div className="access-denied-icon">
        <Ic.AlertTriangle />
      </div>
      <h3>Access Restricted</h3>
      <p>You don't have permission to view this page. Contact your system administrator.</p>
    </div>
  );
}