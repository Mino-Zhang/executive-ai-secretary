import { readdir, readFile, stat } from "node:fs/promises";
import { extname, resolve } from "node:path";

const root = resolve(process.argv[2] ?? "dist");
const forbidden = ["Demo@2026", "Admin@2026", "sk-demo-masked-key", "prototype-data"];
const textExtensions = new Set([".css", ".html", ".js", ".json", ".map", ".mjs", ".txt"]);

async function filesUnder(directory) {
  const output = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) output.push(...await filesUnder(path));
    else output.push(path);
  }
  return output;
}

const rootStat = await stat(root).catch(() => null);
if (!rootStat?.isDirectory()) throw new Error(`Production artifact directory not found: ${root}`);

const findings = [];
for (const path of await filesUnder(root)) {
  if (!textExtensions.has(extname(path))) continue;
  const content = await readFile(path, "utf8");
  for (const token of forbidden) {
    if (content.includes(token)) findings.push(`${path}: ${token}`);
  }
}

if (findings.length) {
  throw new Error(`Production artifact contains Demo-only material:\n${findings.join("\n")}`);
}

process.stdout.write(`Production artifact gate passed (${root}).\n`);
