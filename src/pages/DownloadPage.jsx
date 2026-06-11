import { useState } from "react";
import { Ic } from "../icons/Icons";
import { ROLE_ACCESS } from "../constants/roles";
import AccessDenied from "../components/AccessDenied";
import Spinner from "../components/Spinner";
import { SERVER } from "../constants/config";

export default function DownloadPage({ user, onToast }) {
  if (!ROLE_ACCESS[user.role]?.includes("download")) return <AccessDenied />;

  const [dl, setDl] = useState(false);

  async function handleDl(type) {
    setDl(type);
    try {
      const endpoint = type === "filtered"
        ? `${SERVER}/download/filtered`
        : `${SERVER}/download/raw`;

      const res = await fetch(endpoint, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({}),
      });

      if (!res.ok) {
        const err = await res.json();
        onToast(err.error || "Export failed", "error");
        setDl(false);
        return;
      }

      const blob     = await res.blob();
      const url      = window.URL.createObjectURL(blob);
      const a        = document.createElement("a");
      const filename = type === "filtered"
        ? `filtered_data_${new Date().toISOString().slice(0,10)}.csv`
        : `raw_data_${new Date().toISOString().slice(0,10)}.csv`;
      a.href     = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      onToast(`${type === "filtered" ? "Filtered" : "Raw"} data exported successfully`, "success");

    } catch {
      onToast("Network error — is backend running?", "error");
    }

    setDl(false);
  }

  return (
    <div className="fade-up">

      {/* PAGE HEADER */}
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <div className="page-title">Download Data</div>
            <div className="page-sub">EXPORT SENSOR READINGS · CSV FORMAT</div>
          </div>
        </div>
      </div>

      {/* INFO */}
      <div style={{ padding: "0 32px", marginBottom: 20 }}>
        <div style={{
          background:   "var(--blue-ghost)",
          border:       "1px solid rgba(59,85,168,0.12)",
          borderRadius: "var(--r)",
          padding:      "10px 14px",
          fontSize:     12,
          color:        "var(--text-2)",
          fontFamily:   "var(--mono)",
          display:      "flex",
          alignItems:   "center",
          gap:          8,
        }}>
          <Ic.Database />
          All data exported directly from PostgreSQL database
        </div>
      </div>

      <div className="dl-grid">

        {/* FILTERED */}
        <div className="dl-card">
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <div style={{
              width: 34, height: 34,
              background: "var(--blue-ghost)",
              border: "1px solid rgba(59,85,168,0.12)",
              borderRadius: 8,
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "var(--blue)",
            }}>
              <Ic.Download />
            </div>
            <h3>Filtered Data</h3>
          </div>
          <p>
            Trimmed-mean averaged readings from PostgreSQL.
            Suitable for analysis and reporting.
          </p>
          <button
            className="btn btn-blue"
            style={{ width: "100%", justifyContent: "center", marginTop: 8 }}
            onClick={() => handleDl("filtered")}
            disabled={!!dl}
          >
            {dl === "filtered"
              ? <><Spinner /> Exporting…</>
              : <><Ic.Download /> Export Filtered CSV</>
            }
          </button>
        </div>

        {/* RAW */}
        <div className="dl-card">
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <div style={{
              width: 34, height: 34,
              background: "var(--green-ghost)",
              border: "1px solid rgba(74,122,94,0.2)",
              borderRadius: 8,
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "var(--green)",
            }}>
              <Ic.Database />
            </div>
            <h3>Raw Unfiltered Data</h3>
          </div>
          <p>
            Full-resolution unprocessed readings from PostgreSQL.
            Use for debugging and detailed signal analysis.
          </p>
          <button
            className="btn btn-green"
            style={{ width: "100%", justifyContent: "center", marginTop: 8 }}
            onClick={() => handleDl("raw")}
            disabled={!!dl}
          >
            {dl === "raw"
              ? <><Spinner /> Exporting…</>
              : <><Ic.Download /> Export Raw CSV</>
            }
          </button>
        </div>

      </div>
    </div>
  );
}