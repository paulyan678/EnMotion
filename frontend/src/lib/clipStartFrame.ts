import type { FrameMovementType } from "@/lib/frameMovement";
import { frameMovementTypeFromFrame } from "@/lib/frameMovement";
import { durableMediaReference } from "@/lib/utils";

export interface ClipStartImageVariant {
  id: string;
  url: string;
  source: "rendered" | "storyboard" | "generated" | "upload" | "legacy";
}

interface ClipImageAssetLike {
  selected_id?: string | null;
  variants?: Array<{ id?: string | null; url?: string | null }>;
}

export interface ClipFrameLike {
  rendered_image_asset?: ClipImageAssetLike;
  image_asset?: ClipImageAssetLike;
  t2i_image_urls?: string[];
  t2i_selected_index?: number;
  rendered_image_url?: string;
  image_url?: string;
  clip_start_image_id?: string | null;
  clip_start_image_url?: string | null;
  camera_movement?: string | null;
  camera_movement_structured?: {
    primary?: string | null;
    description?: string | null;
  } | null;
}

/** Synchronous FNV-1a identity mirrored by the backend. */
export function clipImageId(url: string): string {
  const bytes = new TextEncoder().encode(durableMediaReference(url));
  let value = 0x811c9dc5;
  for (const byte of bytes) {
    value ^= byte;
    value = Math.imul(value, 0x01000193) >>> 0;
  }
  return `clip-image-${value.toString(16).padStart(8, "0")}-${bytes.length}`;
}

export function storyboardImageVariants(frame: ClipFrameLike): ClipStartImageVariant[] {
  const byDurableUrl = new Map<string, ClipStartImageVariant>();
  const add = (
    url: unknown,
    id: unknown,
    source: ClipStartImageVariant["source"],
  ) => {
    if (typeof url !== "string" || !url.trim()) return;
    const durable = durableMediaReference(url);
    if (byDurableUrl.has(durable)) return;
    byDurableUrl.set(durable, {
      id: typeof id === "string" && id ? id : clipImageId(url),
      url,
      source,
    });
  };

  for (const variant of frame.rendered_image_asset?.variants ?? []) {
    add(variant?.url, variant?.id, "rendered");
  }
  for (const variant of frame.image_asset?.variants ?? []) {
    add(variant?.url, variant?.id, "storyboard");
  }
  for (const url of frame.t2i_image_urls ?? []) {
    add(url, null, String(url).includes("/uploads/") || String(url).startsWith("uploads/") ? "upload" : "generated");
  }
  add(frame.rendered_image_url, null, "legacy");
  add(frame.image_url, null, "legacy");
  return Array.from(byDurableUrl.values());
}

export function selectedClipStartImage(
  frame: ClipFrameLike,
  variants = storyboardImageVariants(frame),
): ClipStartImageVariant | null {
  const explicitId = frame.clip_start_image_id;
  if (typeof explicitId === "string") {
    const explicit = variants.find((variant) => variant.id === explicitId);
    if (explicit) return explicit;
  }

  const explicitUrl = frame.clip_start_image_url;
  if (typeof explicitUrl === "string") {
    const durable = durableMediaReference(explicitUrl);
    const explicit = variants.find((variant) => durableMediaReference(variant.url) === durable);
    if (explicit) return explicit;
  }

  // The nested selected IDs are the canonical storyboard selections for
  // projects created before clip_start_image_* was added. Prefer them over
  // legacy T2I indices and top-level URL mirrors.
  const selectedIds = [frame.rendered_image_asset?.selected_id, frame.image_asset?.selected_id];
  for (const id of selectedIds) {
    const selected = variants.find((variant) => variant.id === id);
    if (selected) return selected;
  }

  const selectedIndex = Number.isInteger(frame.t2i_selected_index)
    ? Math.max(0, Math.min(frame.t2i_selected_index ?? 0, Math.max(0, (frame.t2i_image_urls?.length ?? 1) - 1)))
    : 0;
  const t2iUrl = frame.t2i_image_urls?.[selectedIndex];
  if (t2iUrl) {
    const durable = durableMediaReference(t2iUrl);
    const selected = variants.find((variant) => durableMediaReference(variant.url) === durable);
    if (selected) return selected;
  }
  return variants[0] ?? null;
}

/**
 * Canonical storyboard artwork selection. Storyboard views prefer the image
 * asset's selected ID; Motion's independently persisted clip-start selection
 * is used only as a migration fallback when a frame has no artwork selection.
 */
export function selectedStoryboardImage(
  frame: ClipFrameLike,
  variants = storyboardImageVariants(frame),
): ClipStartImageVariant | null {
  const selectedIds = [frame.rendered_image_asset?.selected_id, frame.image_asset?.selected_id];
  for (const id of selectedIds) {
    const selected = variants.find((variant) => variant.id === id);
    if (selected) return selected;
  }

  for (const url of [frame.rendered_image_url, frame.image_url]) {
    if (typeof url !== "string" || !url.trim()) continue;
    const durable = durableMediaReference(url);
    const selected = variants.find((variant) => durableMediaReference(variant.url) === durable);
    if (selected) return selected;
  }

  const explicitClip = selectedClipStartImage(frame, variants);
  return explicitClip ?? variants[0] ?? null;
}

/** Backwards-compatible Motion name; both paths intentionally share logic. */
export const clipStartImageVariants = storyboardImageVariants;

export function clipFrameType(frame: ClipFrameLike): FrameMovementType {
  return frameMovementTypeFromFrame(frame as unknown as Record<string, unknown>) ?? "static";
}

export function frameTaskStatus(
  tasks: Array<{ frame_id?: string; status: string; created_at?: number }>,
  frameId: string,
): "queued" | "processing" | "completed" | "failed" | null {
  const frameTasks = tasks
    .filter((task) => task.frame_id === frameId)
    .sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));
  if (frameTasks.some((task) => task.status === "processing")) return "processing";
  if (frameTasks.some((task) => task.status === "pending")) return "queued";
  const latest = frameTasks[0];
  if (!latest) return null;
  return latest.status === "completed" ? "completed" : latest.status === "failed" ? "failed" : null;
}
