import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const publicRoot = path.join(frontendRoot, "public");
const exportRoot = path.join(frontendRoot, "out");
const indexPath = path.join(exportRoot, "index.html");
// The desktop sidecar intentionally serves this per-launch bootstrap from its
// origin root before React loads. Every other asset must remain /static-based.
const allowedRootRelativeAssets = new Set(["runtime-config.js"]);

function walkFiles(root) {
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(root, entry.name);
    return entry.isDirectory() ? walkFiles(fullPath) : [fullPath];
  });
}

if (!fs.existsSync(indexPath)) {
  throw new Error("Static export is missing out/index.html");
}

const publicFiles = walkFiles(publicRoot);
for (const sourcePath of publicFiles) {
  const relative = path.relative(publicRoot, sourcePath);
  if (!fs.existsSync(path.join(exportRoot, relative))) {
    throw new Error(`Static export omitted public asset: ${relative}`);
  }
}

const indexHtml = fs.readFileSync(indexPath, "utf8");
const desktopExport = indexHtml.includes('/static/_next/');
if (desktopExport) {
  const absoluteAttributes = [...indexHtml.matchAll(/(?:src|href)="(\/[^"#?]*)/g)]
    .map((match) => match[1])
    .filter((value) =>
      !value.startsWith("/static/")
      && !allowedRootRelativeAssets.has(value.replace(/^\/+/, "")),
    );
  if (absoluteAttributes.length > 0) {
    throw new Error(
      `Desktop export contains root-relative asset references: ${absoluteAttributes.join(", ")}`,
    );
  }

  const textOutputs = walkFiles(exportRoot).filter((filePath) =>
    /\.(?:html|js|json|css)$/.test(filePath),
  );
  const payload = textOutputs.map((filePath) => fs.readFileSync(filePath, "utf8")).join("\n");
  const rootRelativePublicAssets = publicFiles
    .map((filePath) => path.relative(publicRoot, filePath).split(path.sep).join("/"))
    .filter((relative) => !allowedRootRelativeAssets.has(relative))
    .filter((relative) =>
      payload.includes(`"/${relative}`)
      || payload.includes(`'/${relative}`)
      || payload.includes(`\\"/${relative}`),
    );
  if (rootRelativePublicAssets.length > 0) {
    throw new Error(
      `Desktop chunks contain root-relative public assets: ${rootRelativePublicAssets.join(", ")}`,
    );
  }
}

console.log(`Static export contract passed (${desktopExport ? "desktop" : "container"}).`);
