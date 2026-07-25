import {
  DEFAULT_ACTIVE_MODELS,
  getApprovedModel,
  getApprovedModels,
} from '@/lib/newApiModels';
import type { PlaygroundMode } from './usePlaygroundStore';

const SEEDANCE_STANDARD_MODEL_ID = 'doubao-seedance-2-0-260128';

const T2V_1080P_MODELS = new Set([
  SEEDANCE_STANDARD_MODEL_ID,
]);

const VIDEO_RATIO_OPTIONS = ['16:9', '9:16', '1:1'];
const VIDEO_DEFAULTS = Object.freeze({
  resolution: '720p',
  aspectRatio: '16:9',
  duration: 5,
  generateAudio: true,
  watermark: false,
});

export interface PlaygroundModelOption {
  id: string;
  displayName: string;
  family: string;
  description: string;
  recommended: boolean;
  badges: string[];
  capabilities: string[];
  duration:
    | { type: 'slider'; min: number; max: number; step: number; default: number }
    | { type: 'buttons'; options: number[]; default: number }
    | { type: 'fixed'; value: number }
    | null;
  params: {
    resolution?: { options: string[]; default: string };
    ratio?: { options: string[]; default: string };
    size?: { options: string[]; default: string };
    quality?: { options: string[]; default: string };
    seed?: boolean;
    negativePrompt?: boolean;
    promptExtend?: boolean;
    watermark?: boolean;
  };
  maxReferenceImages: number;
}

function modeCapability(mode: PlaygroundMode): 'image' | 'video' {
  return mode === 't2i' || mode === 'i2i' ? 'image' : 'video';
}

/**
 * The New API contract is narrower than the family-wide catalog entry:
 * image-to-video accepts 720p only, and the Fast model does not accept 1080p.
 * Keep this conservative for new/unknown models until their 1080p support is
 * explicitly verified.
 */
export function getVideoResolutionOptions(
  mode: PlaygroundMode,
  modelId: string,
): string[] {
  return mode === 't2v' && T2V_1080P_MODELS.has(modelId)
    ? ['720p', '1080p']
    : ['720p'];
}

export function getModelsForMode(mode: PlaygroundMode): PlaygroundModelOption[] {
  const capability = modeCapability(mode);
  return getApprovedModels(capability)
    .filter((model) => model.capabilities.includes(mode))
    .map((model) => ({
      id: model.id,
      displayName: model.name,
      family: 'newapi',
      description: model.description,
      recommended: model.id === DEFAULT_ACTIVE_MODELS[capability],
      badges: ['新版 API'],
      capabilities: [...model.capabilities],
      duration: capability === 'video'
        ? { type: 'slider' as const, min: 4, max: 15, step: 1, default: 5 }
        : null,
      params: capability === 'video'
        ? {
            resolution: {
              options: getVideoResolutionOptions(mode, model.id),
              default: VIDEO_DEFAULTS.resolution,
            },
            ratio: {
              options: VIDEO_RATIO_OPTIONS,
              default: VIDEO_DEFAULTS.aspectRatio,
            },
            seed: true,
            promptExtend: false,
            watermark: true,
          }
        : {
            size: {
              options: ['1536x1024', '1024x1024', '1024x1536'],
              default: '1536x1024',
            },
            quality: { options: ['auto', 'high', 'medium', 'low'], default: 'auto' },
          },
      maxReferenceImages: mode === 'i2i' ? 16 : 0,
    }));
}

export function getDefaultModelForMode(mode: PlaygroundMode): string {
  const capability = modeCapability(mode);
  const preferred = DEFAULT_ACTIVE_MODELS[capability];
  const models = getModelsForMode(mode);
  return models.some((model) => model.id === preferred) ? preferred : models[0]?.id ?? '';
}

export function getModelDisplayInfo(
  modelId: string,
): { displayName: string; family: string } | null {
  const model = getApprovedModel(modelId);
  return model ? { displayName: model.name, family: 'newapi' } : null;
}

export function getModelParams(
  modelId: string,
  mode: PlaygroundMode,
): PlaygroundModelOption['params'] | null {
  return getModelsForMode(mode).find((candidate) => candidate.id === modelId)?.params ?? null;
}

export function getModelDuration(
  modelId: string,
): PlaygroundModelOption['duration'] {
  const model = getApprovedModel(modelId);
  return model?.capability === 'video'
    ? { type: 'slider', min: 4, max: 15, step: 1, default: 5 }
    : null;
}

/**
 * Build the exact video parameters sent to the generation queue.
 *
 * The controls render defaults even when the Zustand record is empty. Without
 * materialising those values, the server/provider can apply a different
 * default. This also removes image-only settings left behind when a generated
 * image is reused as an I2V reference.
 */
export function getEffectivePlaygroundParameters(
  mode: PlaygroundMode,
  modelId: string,
  parameters: Record<string, unknown>,
): Record<string, unknown> {
  if (mode !== 't2v' && mode !== 'i2v') return { ...parameters };

  const resolutionOptions = getVideoResolutionOptions(mode, modelId);
  const resolution = typeof parameters.resolution === 'string'
    && resolutionOptions.includes(parameters.resolution)
    ? parameters.resolution
    : VIDEO_DEFAULTS.resolution;

  const aspectRatio = typeof parameters.aspect_ratio === 'string'
    && VIDEO_RATIO_OPTIONS.includes(parameters.aspect_ratio)
    ? parameters.aspect_ratio
    : VIDEO_DEFAULTS.aspectRatio;

  const durationConfig = getModelDuration(modelId);
  let duration: number = VIDEO_DEFAULTS.duration;
  if (
    durationConfig?.type === 'slider'
    && typeof parameters.duration === 'number'
    && Number.isInteger(parameters.duration)
    && parameters.duration >= durationConfig.min
    && parameters.duration <= durationConfig.max
    && (parameters.duration - durationConfig.min) % durationConfig.step === 0
  ) {
    duration = parameters.duration;
  } else if (
    durationConfig?.type === 'buttons'
    && typeof parameters.duration === 'number'
    && durationConfig.options.includes(parameters.duration)
  ) {
    duration = parameters.duration;
  } else if (durationConfig?.type === 'fixed') {
    duration = durationConfig.value;
  }

  const normalized: Record<string, unknown> = {
    resolution,
    aspect_ratio: aspectRatio,
    duration,
    generate_audio: typeof parameters.generate_audio === 'boolean'
      ? parameters.generate_audio
      : VIDEO_DEFAULTS.generateAudio,
    watermark: typeof parameters.watermark === 'boolean'
      ? parameters.watermark
      : VIDEO_DEFAULTS.watermark,
  };

  if (
    typeof parameters.seed === 'number'
    && Number.isFinite(parameters.seed)
    && Number.isInteger(parameters.seed)
  ) {
    normalized.seed = parameters.seed;
  }

  return normalized;
}
