import { assertLocalRequest, readControlBody } from "@/lib/controlSecurity";
import { DOCUMENT_LIMIT, extractDocument } from "@/lib/documents";

export const runtime = "nodejs";

export async function POST(request: Request) {
  // Extract text from a user-selected upload locally; uploading alone does not send it to cloud AI.
  try { assertLocalRequest(request); }
  catch (error) { return Response.json({ ok: false, error: (error as Error).message }, { status: 403 }); }
  try {
    if (Number(request.headers.get("content-length") || 0) > DOCUMENT_LIMIT + 65536) throw new Error("Document upload is too large.");
    const body = await readControlBody(request, DOCUMENT_LIMIT + 65536);
    const data = await new Response(body, { headers: { "Content-Type": request.headers.get("content-type") || "" } }).formData();
    const file = data.get("file");
    if (!(file instanceof File) || file.size > DOCUMENT_LIMIT) throw new Error("Choose a document smaller than 5 MB.");
    return Response.json({ ok: true, name: file.name, ...await extractDocument(Buffer.from(await file.arrayBuffer()), file.name) });
  } catch (error) { return Response.json({ ok: false, error: (error as Error).message }, { status: 400 }); }
}
