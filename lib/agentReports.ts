import { mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

function reportName(filename: string) {
  if (!/^[\w-]+\.md$/.test(filename)) throw new Error("Invalid generated report name.");
  return filename;
}

export async function exportedReportPath(filename: string) {
  // Keep report exports inside Documents even if a folder points somewhere else on disk.
  reportName(filename);
  const documents = await realpath(path.join(/* turbopackIgnore: true */ os.homedir(), "Documents"));
  const folder = path.join(documents, "ULTRON Reports");
  await mkdir(folder, { recursive: true });
  const canonical = await realpath(folder);
  const relative = path.relative(documents, canonical);
  if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Report destination resolves outside Documents.");
  return path.join(canonical, filename);
}

export async function exportReport(filename: string) {
  // Create a new report copy without replacing an existing file, then check its contents.
  reportName(filename);
  const source = path.join(/* turbopackIgnore: true */ process.cwd(), ".ultron", "reports", filename);
  const text = await readFile(source, "utf8");
  const destination = await exportedReportPath(filename);
  await writeFile(destination, text, { flag: "wx" });
  if (await readFile(destination, "utf8") !== text) throw new Error("Export read-back verification failed.");
  return destination;
}
