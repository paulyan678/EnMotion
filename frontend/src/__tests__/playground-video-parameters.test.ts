import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({
  playgroundApi: {
    deleteUpload: vi.fn(),
  },
}));

import {
  getEffectivePlaygroundInputMedia,
  getEffectivePlaygroundParameters,
  getModelsForMode,
} from '@/components/modules/playground/playgroundModels';
import { usePlaygroundStore } from '@/components/modules/playground/usePlaygroundStore';
import { playgroundApi } from '@/lib/api';

const STANDARD_MODEL = 'doubao-seedance-2-0-260128';
const FAST_MODEL = 'doubao-seedance-2-0-fast-260128';
const MINI_MODEL = 'doubao-seedance-2-0-mini-260615';

const VIDEO_DEFAULTS = {
  resolution: '720p',
  aspect_ratio: '16:9',
  duration: 5,
  generate_audio: true,
  watermark: false,
};

afterEach(() => {
  usePlaygroundStore.setState({
    mode: 't2i',
    modelId: 'gpt-image-2',
    inputMedia: [],
    parameters: {},
    modelPreferences: {},
    queue: [],
  });
  vi.mocked(playgroundApi.deleteUpload).mockReset();
});

describe('Playground video parameter contracts', () => {
  it('offers 1080p only for the verified standard text-to-video model', () => {
    const t2vModels = getModelsForMode('t2v');
    const i2vModels = getModelsForMode('i2v');

    expect(t2vModels.find((model) => model.id === STANDARD_MODEL)?.params.resolution)
      .toEqual({ options: ['720p', '1080p'], default: '720p' });
    expect(t2vModels.find((model) => model.id === MINI_MODEL)?.params.resolution)
      .toEqual({ options: ['720p'], default: '720p' });
    expect(t2vModels.find((model) => model.id === FAST_MODEL)?.params.resolution)
      .toEqual({ options: ['720p'], default: '720p' });

    for (const model of i2vModels) {
      expect(model.params.resolution).toEqual({
        options: ['720p'],
        default: '720p',
      });
    }
  });

  it('materialises the video defaults shown by an untouched compose form', () => {
    expect(getEffectivePlaygroundParameters('i2v', FAST_MODEL, {}))
      .toEqual(VIDEO_DEFAULTS);
  });

  it('materialises image catalog defaults and strips stale video controls', () => {
    expect(getEffectivePlaygroundParameters('t2i', 'gpt-image-2', {
      resolution: '1080p',
      duration: 15,
      watermark: true,
    })).toEqual({
      size: '1536x1024',
      quality: 'auto',
    });
  });

  it('replaces unsupported values and removes stale image-only parameters', () => {
    expect(getEffectivePlaygroundParameters('i2v', STANDARD_MODEL, {
      size: '1024x1024',
      quality: 'high',
      prompt_extend: true,
      resolution: '1080p',
      aspect_ratio: '4:3',
      duration: 30,
      generate_audio: false,
      watermark: true,
      seed: 42,
    })).toEqual({
      ...VIDEO_DEFAULTS,
      generate_audio: false,
      watermark: true,
      seed: 42,
    });
  });

  it('normalises image settings immediately when a result becomes an I2V input', () => {
    usePlaygroundStore.setState({
      mode: 't2i',
      modelId: 'gpt-image-2',
      inputMedia: [],
      parameters: {
        size: '1024x1024',
        quality: 'high',
        resolution: '1080p',
      },
      modelPreferences: { i2v: STANDARD_MODEL },
    });

    usePlaygroundStore.getState().useResultAsReference(
      'playground/images/generated.png',
      'image',
      'i2v',
    );

    const state = usePlaygroundStore.getState();
    expect(state.mode).toBe('i2v');
    expect(state.modelId).toBe(STANDARD_MODEL);
    expect(state.inputMedia).toEqual(['playground/images/generated.png']);
    expect(state.parameters).toEqual(VIDEO_DEFAULTS);
  });

  it('clears stale text-mode media and limits I2V to one first frame', () => {
    expect(getEffectivePlaygroundInputMedia('t2v', ['stale.png'])).toEqual([]);
    expect(getEffectivePlaygroundInputMedia('t2i', ['stale.png'])).toEqual([]);
    expect(getEffectivePlaygroundInputMedia('i2v', ['first.png', 'second.png']))
      .toEqual(['first.png']);
  });

  it('sanitises parameters and media when switching modes', () => {
    usePlaygroundStore.setState({
      mode: 'i2i',
      modelId: 'gpt-image-2',
      inputMedia: ['reference.png'],
      parameters: { size: '1024x1024', quality: 'high', resolution: '1080p' },
      modelPreferences: { t2v: FAST_MODEL },
    });

    usePlaygroundStore.getState().setMode('t2v');

    expect(usePlaygroundStore.getState()).toMatchObject({
      mode: 't2v',
      modelId: FAST_MODEL,
      inputMedia: [],
      parameters: VIDEO_DEFAULTS,
    });
  });

  it('reclaims only owned uploads dropped by an I2V mode switch', async () => {
    const retained = 'playground/uploads/first.png';
    const dropped = 'playground/uploads/second.png';
    const libraryAsset = 'assets/character/library-reference.png';
    vi.mocked(playgroundApi.deleteUpload).mockResolvedValue({ ok: true });
    usePlaygroundStore.setState({
      mode: 'i2i',
      modelId: 'gpt-image-2',
      inputMedia: [retained, dropped, libraryAsset],
      parameters: {},
      modelPreferences: { i2v: FAST_MODEL },
    });

    usePlaygroundStore.getState().setMode('i2v');

    expect(usePlaygroundStore.getState().inputMedia).toEqual([retained]);
    await vi.waitFor(() => {
      expect(playgroundApi.deleteUpload).toHaveBeenCalledTimes(1);
    });
    expect(playgroundApi.deleteUpload).toHaveBeenCalledWith(dropped);
    expect(playgroundApi.deleteUpload).not.toHaveBeenCalledWith(libraryAsset);
  });

  it('preserves owned uploads referenced by a queued generation', async () => {
    const queuedUpload = 'playground/uploads/queued-reference.png';
    vi.mocked(playgroundApi.deleteUpload).mockResolvedValue({ ok: true });
    usePlaygroundStore.setState({
      mode: 'i2i',
      modelId: 'gpt-image-2',
      inputMedia: [queuedUpload],
      parameters: {},
      modelPreferences: { t2v: FAST_MODEL },
      queue: [{
        id: 'queued-request',
        mode: 'i2i',
        modelId: 'gpt-image-2',
        prompt: 'Queued image edit',
        inputMedia: [queuedUpload],
        parameters: { size: '1536x1024', quality: 'auto' },
        batchSize: 1,
        status: 'pending',
        enqueuedAt: 1,
      }],
    });

    usePlaygroundStore.getState().setMode('t2v');
    usePlaygroundStore.getState().resetInput();

    expect(usePlaygroundStore.getState().inputMedia).toEqual([]);
    await Promise.resolve();
    expect(playgroundApi.deleteUpload).not.toHaveBeenCalled();
    expect(usePlaygroundStore.getState().queue[0].inputMedia).toEqual([queuedUpload]);

    usePlaygroundStore.getState().removeFromQueue('queued-request');
    await vi.waitFor(() => {
      expect(playgroundApi.deleteUpload).toHaveBeenCalledWith(queuedUpload);
    });
  });

  it('reverts T2V to a visible image-edit state when cleanup fails', async () => {
    const upload = 'playground/uploads/retry-visible.png';
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(playgroundApi.deleteUpload).mockRejectedValueOnce(new Error('cleanup unavailable'));
    usePlaygroundStore.setState({
      mode: 'i2i',
      modelId: 'gpt-image-2',
      inputMedia: [upload],
      parameters: {},
      modelPreferences: { t2v: FAST_MODEL, i2i: 'gpt-image-2' },
      queue: [],
    });

    usePlaygroundStore.getState().setMode('t2v');
    expect(usePlaygroundStore.getState()).toMatchObject({
      mode: 't2v',
      inputMedia: [],
    });

    await vi.waitFor(() => {
      expect(usePlaygroundStore.getState()).toMatchObject({
        mode: 'i2i',
        inputMedia: [upload],
      });
    });
    consoleError.mockRestore();
  });

  it('reverts I2V when a failed cleanup would hide its second image', async () => {
    const first = 'playground/uploads/first-visible.png';
    const second = 'playground/uploads/second-visible.png';
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(playgroundApi.deleteUpload).mockRejectedValueOnce(new Error('cleanup unavailable'));
    usePlaygroundStore.setState({
      mode: 'i2i',
      modelId: 'gpt-image-2',
      inputMedia: [first, second],
      parameters: {},
      modelPreferences: { i2v: FAST_MODEL, i2i: 'gpt-image-2' },
      queue: [],
    });

    usePlaygroundStore.getState().setMode('i2v');
    expect(usePlaygroundStore.getState()).toMatchObject({
      mode: 'i2v',
      inputMedia: [first],
    });

    await vi.waitFor(() => {
      expect(usePlaygroundStore.getState()).toMatchObject({
        mode: 'i2i',
        inputMedia: [first, second],
      });
    });
    consoleError.mockRestore();
  });
});
