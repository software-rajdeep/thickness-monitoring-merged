import { useEffect } from "react";
import { Ic } from "../icons/Icons";

export default function Toast({ msg, type, onDone }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3000);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className={`toast ${type}`}>
      <span className="toast-icon">
        {type === "success"
          ? <span style={{ color: "var(--green)" }}><Ic.Check /></span>
          : <span style={{ color: "var(--red)" }}><Ic.X /></span>
        }
      </span>
      <span>{msg}</span>
    </div>
  );
}