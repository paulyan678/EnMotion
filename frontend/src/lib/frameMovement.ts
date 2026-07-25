export const FRAME_MOVEMENT_TYPES = [
  "static",
  "push_in",
  "pull_out",
  "pan_left",
  "pan_right",
  "tilt_up",
  "tilt_down",
  "orbit",
  "follow",
  "crane_up",
  "crane_down",
  "handheld",
  "zoom_in",
  "zoom_out",
] as const;

export type FrameMovementType = (typeof FRAME_MOVEMENT_TYPES)[number];

const FRAME_MOVEMENT_TYPE_SET = new Set<string>(FRAME_MOVEMENT_TYPES);

const FRAME_MOVEMENT_ALIASES: Record<string, FrameMovementType> = {
  "静止": "static",
  "固定": "static",
  "固定镜头": "static",
  "固定机位": "static",
  "static_camera": "static",
  "推进": "push_in",
  "推镜": "push_in",
  "推镜头": "push_in",
  "缓慢推进": "push_in",
  "快速推镜": "push_in",
  "dolly_in": "push_in",
  "拉远": "pull_out",
  "拉镜": "pull_out",
  "拉镜头": "pull_out",
  "快速拉远": "pull_out",
  "dolly_out": "pull_out",
  "左摇": "pan_left",
  "向左摇摄": "pan_left",
  "右摇": "pan_right",
  "向右摇摄": "pan_right",
  "上摇": "tilt_up",
  "向上摇摄": "tilt_up",
  "下摇": "tilt_down",
  "向下摇摄": "tilt_down",
  "环绕": "orbit",
  "环绕旋转": "orbit",
  "跟拍": "follow",
  "跟随": "follow",
  "跟随平移": "follow",
  "tracking": "follow",
  "tracking_shot": "follow",
  "升镜": "crane_up",
  "缓慢上升": "crane_up",
  "降镜": "crane_down",
  "手持": "handheld",
  "手持拍摄": "handheld",
  "变焦推": "zoom_in",
  "变焦推近": "zoom_in",
  "变焦拉": "zoom_out",
  "变焦拉远": "zoom_out",
};

function normalizedAlias(value: string): string {
  return value.trim().toLowerCase().replace(/[\s-]+/g, "_");
}

export function normalizeFrameMovement(
  value: unknown,
): FrameMovementType | null {
  if (typeof value !== "string" || !value.trim()) return null;

  const normalized = normalizedAlias(value);
  if (FRAME_MOVEMENT_TYPE_SET.has(normalized)) {
    return normalized as FrameMovementType;
  }
  return FRAME_MOVEMENT_ALIASES[normalized] ?? null;
}

export function frameMovementTypeFromFrame(
  frame: Record<string, unknown> | null | undefined,
): FrameMovementType | null {
  if (!frame) return null;

  const structured = frame.camera_movement_structured as
    | { primary?: unknown; description?: unknown }
    | null
    | undefined;
  const candidates = [
    frame.camera_movement,
    structured?.primary,
    structured?.description,
  ];

  for (const candidate of candidates) {
    const normalized = normalizeFrameMovement(candidate);
    if (normalized) return normalized;
  }
  return null;
}

export function rawFrameMovement(
  frame: Record<string, unknown> | null | undefined,
): string | null {
  if (!frame) return null;
  const structured = frame.camera_movement_structured as
    | { primary?: unknown; description?: unknown }
    | null
    | undefined;
  const candidate =
    frame.camera_movement ?? structured?.description ?? structured?.primary;
  return typeof candidate === "string" && candidate.trim()
    ? candidate.trim()
    : null;
}
