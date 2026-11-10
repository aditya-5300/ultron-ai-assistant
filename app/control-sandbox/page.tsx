"use client";

import { useState } from "react";

export default function ControlSandbox() {
  const [name, setName] = useState("");
  const [message, setMessage] = useState("No action submitted.");
  return <main style={{ padding: 40, background: "#071627", color: "#d8efff", minHeight: "100vh", overflow: "auto", fontFamily: "Segoe UI, sans-serif" }}>
    <h1>ULTRON browser control sandbox</h1><p>Local test form only. No data is saved or sent anywhere.</p>
    <form onSubmit={event => { event.preventDefault(); setMessage(`Hello, ${name || "explorer"}. Test form submitted locally.`); }} style={{ display: "grid", gap: 16, maxWidth: 400, marginTop: 30 }}>
      <label htmlFor="sandbox-name">Test name</label><input id="sandbox-name" value={name} onChange={event => setName(event.target.value)} style={{ padding: 12 }} />
      <button type="submit" style={{ padding: 12 }}>Submit test form</button>
    </form><p role="status" style={{ marginTop: 30 }}>{message}</p>
  </main>;
}
