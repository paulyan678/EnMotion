import { afterEach, describe, expect, it } from 'vitest';

import {
  getEffectivePlaygroundParameters,
  getModelsForMode,
} from '@/components/modules/playground/playgroundModels';
import { usePlaygroundStore } from '@/components/modules/playground/usePlaygroundStore';

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
});
