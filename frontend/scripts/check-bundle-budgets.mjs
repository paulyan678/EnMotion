#!/usr/bin/env node

import { readdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputRoot = path.join(frontendRoot, "out");
const budgets = {
  totalBytes: 8 * 1024 * 1024,
  javascriptBytes: Math.floor(3.4 * 1024 * 1024),
  largestJavascriptBytes: 950_000,
  cssBytes: 160 * 1024,
};

async function filesBelow(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const absolutePath = path.join(directory, entry.name);
    return entry.isDirectory() ? filesBelow(absolutePath) : [absolutePath];
  }));
  return nested.flat();
}

function formatMiB(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`;
}

const files = await filesBelow(outputRoot);
const measured = {
  totalBytes: 0,
  javascriptBytes: 0,
  largestJavascriptBytes: 0,
  cssBytes: 0,
};

for (const file of files) {
  const size = (await stat(file)).size;
  measured.totalBytes += size;
  if (file.endsWith(".js")) {
    measured.javascriptBytes += size;
    measured.largestJavascriptBytes = Math.max(measured.largestJavascriptBytes, size);
  }
  if (file.endsWith(".css")) measured.cssBytes += size;
}

const failures = Object.entries(budgets).flatMap(([metric, budget]) => (
  measured[metric] > budget
    ? [`${metric}: ${measured[metric]} bytes exceeds ${budget}`]
    : []
));

console.log(JSON.stringify({ measured, budgets }, null, 2));
console.log(
  `Frontend export ${formatMiB(measured.totalBytes)}, JavaScript ${formatMiB(measured.javascriptBytes)}, CSS ${formatMiB(measured.cssBytes)}`,
);
if (failures.length > 0) throw new Error(failures.join("\n"));
