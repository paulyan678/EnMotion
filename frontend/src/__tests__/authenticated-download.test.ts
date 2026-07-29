// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { saveAuthenticatedMedia } from '@/lib/download';

describe('authenticated media download', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:download'),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('rejects a protected non-success response without opening a raw URL', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('unauthorized', { status: 401 }));
    const open = vi.spyOn(window, 'open');

    await expect(
      saveAuthenticatedMedia('https://studio.example/files/private.png', 'private.png'),
    ).rejects.toThrow('401');

    expect(open).not.toHaveBeenCalled();
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  });

  it('downloads a successful authenticated response through an object URL', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(
      new Blob(['image'], { type: 'image/png' }),
      { status: 200 },
    ));

    await saveAuthenticatedMedia(
      'https://studio.example/files/private.png',
      'private.png',
    );

    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce();
  });
});
