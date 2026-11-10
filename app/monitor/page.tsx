import MonitoringDashboard from "@/components/MonitoringDashboard";

export const metadata = { title: "ULTRON · System Observatory", description: "Local system telemetry, explainable diagnoses and permission-gated corrective actions." };
// Keep health monitoring on its own page so it does not need the heavier 3D assistant screen.
export default function MonitorPage() { return <MonitoringDashboard />; }
