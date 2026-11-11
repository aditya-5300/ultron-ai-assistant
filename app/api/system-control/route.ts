import { assertLocalRequest, readControlBody, recordControl, validateControlPayload } from "@/lib/controlSecurity";
import { executeSystemAction } from "@/lib/systemActions";

export const runtime = "nodejs";

export async function POST(request: Request) {
  // This local entry point checks the request before handing it to a supported controller.
  try { assertLocalRequest(request); }
  catch (error) { return Response.json({ ok: false, error: (error as Error).message }, { status: 403 }); }
  let action = "invalid";
  try {
    if (Number(request.headers.get("content-length") || 0) > 16000) throw new Error("Control request is too large.");
    const payload = validateControlPayload(JSON.parse((await readControlBody(request, 16000)).toString("utf8")));
    action = payload.action;
    const origin = `${new URL(request.url).protocol}//${request.headers.get("host")}`;
    const result = action.startsWith("browser") ? await (await import("@/lib/browserControl")).executeBrowserControl(payload, origin) : await executeSystemAction(payload);
    if ("approval" in result && result.approval) return Response.json(result, { status: 202 });
    if (action !== "powerStatus") recordControl(action, true, "Action completed; inspect the returned result. Launch acceptance does not guarantee a visible window.");
    return Response.json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "System action failed.";
    recordControl(action, false, "Action failed. Details are shown to the user, not retained in this audit log.");
    return Response.json({ ok: false, error: message }, { status: 400 });
  }
}
