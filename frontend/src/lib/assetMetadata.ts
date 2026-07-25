/** Keep visual priority inside the backend-supported 1–5 integer range. */
export function normalizeVisualWeight(value: unknown): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 1;
  return Math.min(5, Math.max(1, Math.round(numeric)));
}
