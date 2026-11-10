import path from "node:path";

export const DOCUMENT_LIMIT = 5 * 1024 * 1024;
export const TEXT_LIMIT = 24000;
export const DOCUMENT_EXTENSIONS = new Set([".pdf", ".docx", ".txt", ".md", ".json", ".js", ".ts", ".tsx", ".css", ".html", ".py", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".yml", ".yaml", ".log", ".csv"]);

export async function extractDocument(buffer: Buffer, filename: string) {
  // Read supported documents locally with size and page limits before offering text to the assistant.
  if (buffer.length > DOCUMENT_LIMIT) throw new Error("Choose a document smaller than 5 MB.");
  const extension = path.extname(filename).toLowerCase();
  if (!DOCUMENT_EXTENSIONS.has(extension)) throw new Error("Supported documents: PDF, DOCX, text and code files.");
  let text: string;
  let pagesLimited = false;
  if (extension === ".pdf") {
    const { PDFParse } = await import("pdf-parse");
    const parser = new PDFParse({ data: new Uint8Array(buffer), isEvalSupported: false });
    try { const result = await parser.getText({ first: 30 }); text = result.text; pagesLimited = result.total > 30; }
    finally { await parser.destroy(); }
  } else if (extension === ".docx") {
    const mammoth = await import("mammoth");
    text = (await mammoth.extractRawText({ buffer })).value;
  } else text = buffer.toString("utf8");
  if (!text.trim()) throw new Error("No readable text found. Scanned images require OCR, which is not supported.");
  return { text: text.slice(0, TEXT_LIMIT), truncated: text.length > TEXT_LIMIT || pagesLimited, summary: summarizeText(text.slice(0, TEXT_LIMIT)), summaryKind: "local-extractive" };
}

export function summarizeText(text: string) {
  // Pick useful sentences from the original text; this local summary does not call a cloud AI.
  const sentences = text.replace(/\s+/g, " ").trim().match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [];
  const stop = new Set("this that with from have will your they their about which when there these would could should into also were been what where the and for are was you not but can has its our all".split(" "));
  const words = (sentence: string) => (sentence.toLowerCase().match(/[a-z]{3,}/g) || []).filter(word => !stop.has(word));
  const frequencies = new Map<string, number>();
  sentences.forEach(sentence => words(sentence).forEach(word => frequencies.set(word, (frequencies.get(word) || 0) + 1)));
  return sentences.map((sentence, index) => ({ sentence: sentence.trim(), index, score: words(sentence).reduce((score, word) => score + (frequencies.get(word) || 0), 0) / Math.sqrt(Math.max(words(sentence).length, 1)) }))
    .sort((first, second) => second.score - first.score).slice(0, 5).sort((first, second) => first.index - second.index).map(item => item.sentence).join(" ").slice(0, 1800);
}
