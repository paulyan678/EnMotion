import { apiFetch } from '@/lib/httpClient';

/**
 * Save a protected media URL through EnMotion's authenticated fetch boundary.
 * Rejects non-success and empty responses; callers own user-facing errors.
 */
export async function saveAuthenticatedMedia(
  url: string,
  filename: string,
): Promise<void> {
  if (!url) throw new Error('Missing media URL');

  const response = await apiFetch(url);
  if (!response.ok) {
    throw new Error(`Authenticated media download failed (${response.status})`);
  }
  const blob = await response.blob();
  if (blob.size === 0) throw new Error('Authenticated media download was empty');

  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename || 'enmotion-output';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
}
