export const ROLE_ACCESS = {
  superadmin: ["dashboard", "sensor-config", "run-mode", "download", "backend"],
  admin:      ["dashboard", "sensor-config", "run-mode", "download"],
  supervisor: ["dashboard", "run-mode", "download"],
  worker:     ["dashboard", "run-mode"],
};

export const ROLE_COLOR = {
  superadmin: "superadmin",
  admin:      "admin",
  supervisor: "supervisor",
  worker:     "worker",
};