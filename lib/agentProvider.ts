import { providerError } from './providerError';
import { cancellableResponse } from './responseCancellation';

export async function agentCompletion(instructions: string, input: string, json = false, signal?: AbortSignal): Promise<string> {
  // Contact the chosen AI provider from the server so API keys never need to reach the browser.
  const cancellation = cancellableResponse(signal);
  try {
  const provider = (process.env.AI_PROVIDER || (process.env.GEMINI_API_KEY ? "gemini" : "openai")).toLowerCase();
  const timeout = AbortSignal.timeout(60000);
  const requestSignal = AbortSignal.any([cancellation.signal, timeout]);
  let response: Response;
  if (provider === "gemini") {
    const key = process.env.GEMINI_API_KEY;
    const model = process.env.GEMINI_MODEL;
    if (!key || key.includes("your-") || !model) throw new Error("Set a valid GEMINI_API_KEY and supported GEMINI_MODEL in .env.local, then restart. No task was executed.");
    response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
      method: "POST", signal: requestSignal, headers: { "Content-Type": "application/json", "x-goog-api-key": key },
      body: JSON.stringify({ systemInstruction: { parts: [{ text: instructions }] }, contents: [{ role: "user", parts: [{ text: input }] }], generationConfig: { temperature: 0.2, maxOutputTokens: 6000, ...(json ? { responseMimeType: "application/json" } : {}) } }),
    });
    if (!response.ok) { await response.body?.cancel(); throw new Error(providerError('Gemini', response.status)); }
    const body = await response.json();
    const output = body.candidates?.[0]?.content?.parts?.map((part: { text?: string }) => part.text || "").join("");
    if (!output) throw new Error("Gemini returned no text. Review provider safety/model settings.");
    return output;
  }
  if (!process.env.OPENAI_API_KEY || process.env.OPENAI_API_KEY.includes("your-")) throw new Error("OPENAI_API_KEY is missing. Configure a key with API credit, or select Gemini.");
  response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST", signal: requestSignal, headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.OPENAI_API_KEY}` },
    body: JSON.stringify({ model: process.env.OPENAI_MODEL || "gpt-4.1-mini", instructions, input, max_output_tokens: 6000, store: false, ...(json ? { text: { format: { type: "json_object" } } } : {}) }),
  });
  if (!response.ok) { await response.body?.cancel(); throw new Error(providerError('OpenAI', response.status)); }
  const body = await response.json();
  const output = body.output?.flatMap((item: { content?: { text?: string }[] }) => item.content || []).map((item: { text?: string }) => item.text || "").join("");
  if (!output || body.status === "incomplete") throw new Error("OpenAI returned an empty or incomplete response.");
  return output;
  } finally { cancellation.dispose(); }
}
