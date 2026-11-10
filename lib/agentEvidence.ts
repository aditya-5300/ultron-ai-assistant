import type { AgentEvidence } from "./agentTypes";
import type { ControlReply } from "./controlTypes";

export function toolEvidence(tool: string, result: ControlReply): AgentEvidence {
  // Separate a checked result from a request Windows merely accepted, so the UI does not overpromise.
  if (!result.ok || result.approval) throw new Error("A failed or unapproved result cannot be marked complete.");
  let state: AgentEvidence["state"] = "acknowledged";
  let detail = "The tool accepted the request. Its external effect was not independently verified.";
  if (result.browser && ["browserOpen", "browserNewTab", "webSearch", "apiGet", "browserInspect", "browserBack", "browserScroll"].includes(tool)) {
    state = "verified";
    detail = `Observed browser page: ${result.browser.title} (${result.browser.url}). This does not verify page claims or transaction completion.`;
  } else if (tool === "searchFiles" && Array.isArray(result.results)) {
    state = "verified";
    detail = `File search returned ${result.results.length} matches within its configured scan limits.`;
  } else if (tool === "knowledgeSearch" && result.text !== undefined) {
    state = "verified";
    detail = "Local knowledge retrieval completed. Retrieved claims are not independently fact-checked.";
  } else if (tool === "makeReport" || tool === "exportReport") {
    state = "verified";
    detail = "The newly saved report was read back and matched the generated content. Report claims still depend on source quality.";
  } else if (tool === "adbDevices" && Array.isArray(result.devices)) {
    state = "verified";
    detail = `ADB returned ${result.devices.length} device entries. Authorization and health are separate checks.`;
  } else if (tool === "adbScreenshot" && result.dataUrl?.startsWith("data:image/png;base64,")) {
    state = "verified";
    detail = "A PNG screen capture was returned by the connected device.";
  }
  return { state, detail, time: new Date().toISOString() };
}
