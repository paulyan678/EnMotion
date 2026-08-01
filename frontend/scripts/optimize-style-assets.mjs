#!/usr/bin/env node

import { access, readdir, readFile, stat, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDirectory, "..");
const repositoryRoot = path.resolve(frontendRoot, "..");
const assetDirectory = path.join(frontendRoot, "public", "assets", "styles");
const presetPath = path.join(repositoryRoot, "src", "apps", "comic_gen", "style_presets.json");
const writeMode = process.argv.includes("--write");
const MAX_DIMENSION = 960;
const MAX_TOTAL_BYTES = 2 * 1024 * 1024;

async function optimizePngs() {
  const pngs = (await readdir(assetDirectory)).filter((name) => name.endsWith(".png"));
  for (const name of pngs) {
    const source = path.join(assetDirectory, name);
    const destination = path.join(assetDirectory, name.replace(/\.png$/i, ".webp"));
    await sharp(source)
      .rotate()
      .resize({
        width: MAX_DIMENSION,
        height: MAX_DIMENSION,
        fit: "inside",
        withoutEnlargement: true,
      })
      .webp({ quality: 84, effort: 6, smartSubsample: true })
      .toFile(destination);
    await unlink(source);
  }

  if (pngs.length > 0) {
    const source = await readFile(presetPath, "utf8");
    await writeFile(presetPath, source.replace(/(\/assets\/styles\/[^"\n]+)\.png/g, "$1.webp"));
  }
}

async function verifyAssets() {
  const presetDocument = JSON.parse(await readFile(presetPath, "utf8"));
  const thumbnails = presetDocument.presets.map((preset) => preset.thumbnail);
  const errors = [];
  let totalBytes = 0;

  if (new Set(thumbnails).size !== thumbnails.length) {
    errors.push("style preset thumbnails must be unique");
  }

  for (const thumbnail of thumbnails) {
    if (typeof thumbnail !== "string" || !thumbnail.startsWith("/assets/styles/")) {
      errors.push(`invalid style thumbnail path: ${String(thumbnail)}`);
      continue;
    }
    if (!thumbnail.endsWith(".webp")) {
      errors.push(`style thumbnail is not WebP: ${thumbnail}`);
      continue;
    }
    const filename = path.basename(thumbnail);
    const absolutePath = path.join(assetDirectory, filename);
    try {
      await access(absolutePath);
      const [metadata, fileStat] = await Promise.all([sharp(absolutePath).metadata(), stat(absolutePath)]);
      totalBytes += fileStat.size;
      if ((metadata.width ?? 0) > MAX_DIMENSION || (metadata.height ?? 0) > MAX_DIMENSION) {
        errors.push(`${filename} exceeds ${MAX_DIMENSION}px: ${metadata.width}x${metadata.height}`);
      }
    } catch (error) {
      errors.push(`missing or unreadable style thumbnail ${filename}: ${error.message}`);
    }
  }

  const shippedFiles = await readdir(assetDirectory);
  const unreferenced = shippedFiles.filter(
    (name) => !thumbnails.includes(`/assets/styles/${name}`),
  );
  if (unreferenced.length > 0) {
    errors.push(`unreferenced style assets: ${unreferenced.join(", ")}`);
  }
  if (totalBytes > MAX_TOTAL_BYTES) {
    errors.push(`style thumbnails use ${totalBytes} bytes; budget is ${MAX_TOTAL_BYTES}`);
  }

  if (errors.length > 0) {
    throw new Error(errors.join("\n"));
  }
  console.log(
    `Style assets verified: ${thumbnails.length} WebP files, ${(totalBytes / 1024 / 1024).toFixed(2)} MiB`,
  );
}

if (writeMode) await optimizePngs();
await verifyAssets();
