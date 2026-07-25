const STATIC_EXPORT_PREFIX = "/static";

/**
 * Resolve assets copied from `frontend/public`.
 *
 * Next.js serves these files from `/assets` in development, while the
 * production static export is mounted below `/static`.
 */
export function getBundledAssetUrl(value: string): string {
  if (!value.startsWith("/assets/")) {
    return value;
  }
  return process.env.NODE_ENV === "production"
    ? `${STATIC_EXPORT_PREFIX}${value}`
    : value;
}
