import React from "react";

const Ic = {
  Logo: () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" />
    </svg>
  ),

  Dashboard: () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <rect x="4" y="4" width="7" height="7" stroke="currentColor" />
      <rect x="13" y="4" width="7" height="7" stroke="currentColor" />
      <rect x="4" y="13" width="7" height="7" stroke="currentColor" />
      <rect x="13" y="13" width="7" height="7" stroke="currentColor" />
    </svg>
  ),

  Sensor: () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <path d="M12 2v20M2 12h20" stroke="currentColor" />
    </svg>
  )
};

export default Ic;
