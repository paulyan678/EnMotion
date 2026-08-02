import { create } from 'zustand';
import {
  DEFAULT_ACTIVE_MODELS,
  isApprovedModelForCapability,
  normalizeActiveModel,
} from '@/lib/newApiModels';
import { readWorkspaceItem, writeWorkspaceItem } from '@/lib/workspaceStorage';
import { playgroundApi, type CompiledGenerationRequest } from '@/lib/api';
import {
  getEffectivePlaygroundInputMedia,
  getEffectivePlaygroundParameters,
} from './playgroundModels';

// ---------------------------------------------------------------------------
// Featured (best-of-batch) persistence — client-side localStorage only.
// Map of generationId -> the one outputId marked "featured" within that batch.
// ---------------------------------------------------------------------------

const FEATURED_LS_KEY = 'enmotion:playground:featured';

function loadFeatured(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  try {
    return JSON.parse(readWorkspaceItem(FEATURED_LS_KEY) || '{}') as Record<string, string>;
  } catch {
    return {};
  }
}

function saveFeatured(map: Record<string, string>): void {
  if (typeof window === 'undefined') return;
  try {
    writeWorkspaceItem(FEATURED_LS_KEY, JSON.stringify(map));
  } catch {
    /* ignore quota / serialization errors */
  }
}

// ---------------------------------------------------------------------------
// Generation queue — client-side concurrency gate. Default concurrency is
// persisted to localStorage; queued ids use a simple module counter.
// ---------------------------------------------------------------------------

const CONCURRENCY_LS_KEY = 'enmotion:playground:concurrency';
const DEFAULT_CONCURRENCY = 3;

function loadConcurrency(): number {
  if (typeof window === 'undefined') return DEFAULT_CONCURRENCY;
  const raw = Number(readWorkspaceItem(CONCURRENCY_LS_KEY));
  return Number.isFinite(raw) && raw >= 1 && raw <= 8 ? raw : DEFAULT_CONCURRENCY;
}

function saveConcurrency(n: number): void {
  if (typeof window === 'undefined') return;
  try {
    writeWorkspaceItem(CONCURRENCY_LS_KEY, String(n));
  } catch {
    /* ignore */
  }
}

let queueSeq = 0;

const MODEL_PREFERENCES_LS_KEY = 'enmotion:playground:model-preferences';

function isOwnedPlaygroundUpload(path: string): boolean {
  if (/^(https?:|data:|blob:)/i.test(path)) return false;
  return /^(?:output\/)?playground\/uploads\/[^/]/.test(path.replace(/\\/g, '/'));
}

function droppedOwnedUploads(
  previous: string[],
  next: string[],
  queued: QueuedRequest[],
): string[] {
  const queuedReferences = new Set(queued.flatMap((request) => request.inputMedia));
  return Array.from(new Set(
    previous.filter(
      (path) => (
        isOwnedPlaygroundUpload(path)
        && !next.includes(path)
        && !queuedReferences.has(path)
      ),
    ),
  ));
}

export function isPlaygroundMediaReferencedByQueue(path: string): boolean {
  return usePlaygroundStore.getState().queue.some(
    (request) => request.inputMedia.includes(path),
  );
}

function loadModelPreferences(): Partial<Record<PlaygroundMode, string>> {
  if (typeof window === 'undefined') return {};
  try {
    const parsed = JSON.parse(readWorkspaceItem(MODEL_PREFERENCES_LS_KEY) || '{}') as Record<string, unknown>;
    const result: Partial<Record<PlaygroundMode, string>> = {};
    for (const mode of ['t2i', 'i2i', 't2v', 'i2v'] as const) {
      const capability = mode === 't2i' || mode === 'i2i' ? 'image' : 'video';
      if (typeof parsed[mode] === 'string' && isApprovedModelForCapability(parsed[mode] as string, capability)) {
        result[mode] = parsed[mode] as string;
      }
    }
    return result;
  } catch {
    return {};
  }
}

function saveModelPreferences(preferences: Partial<Record<PlaygroundMode, string>>): void {
  if (typeof window === 'undefined') return;
  try {
    writeWorkspaceItem(MODEL_PREFERENCES_LS_KEY, JSON.stringify(preferences));
  } catch {
    /* ignore quota / privacy-mode failures */
  }
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PlaygroundMode = 't2i' | 'i2i' | 't2v' | 'i2v';

export interface PlaygroundOutput {
  id: string;
  media_path: string;
  media_type: 'image' | 'video';
  thumbnail_path?: string;
  saved_to_library: boolean;
  library_category?: 'character' | 'scene' | 'prop';
}

export interface PlaygroundGeneration {
  id: string;
  mode: PlaygroundMode;
  model_id: string;
  prompt: string;
  negative_prompt?: string;
  input_media: string[];
  parameters: Record<string, any>;
  compiled_request?: CompiledGenerationRequest | null;
  batch_size: number;
  outputs: PlaygroundOutput[];
  status: 'pending' | 'processing' | 'completed' | 'failed';
  error?: string;
  error_code?: string | null;
  error_diagnostic?: string | null;
  provider_name?: string | null;
  provider_task_id?: string | null;
  provider_request_id?: string | null;
  created_at: string;
  updated_at?: string;
  finished_at?: string | null;
}

export interface PlaygroundTemplate {
  id: string;
  name: string;
  category: string;
  prompt: string;
  negative_prompt?: string;
  default_mode?: PlaygroundMode;
  default_model_id?: string;
  default_parameters: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface QueuedRequest {
  id: string;
  mode: PlaygroundMode;
  modelId: string;
  prompt: string;
  negativePrompt?: string;
  inputMedia: string[];
  parameters: Record<string, any>;
  batchSize: number;
  compiledRequestChecksum?: string;
  status: 'pending' | 'dispatching';
  enqueuedAt: number;
}

// ---------------------------------------------------------------------------
// State & Actions
// ---------------------------------------------------------------------------

interface PlaygroundState {
  // Current input
  mode: PlaygroundMode;
  modelId: string;
  prompt: string;
  negativePrompt: string;
  inputMedia: string[];
  parameters: Record<string, any>;
  batchSize: number;

  // Model preferences (mode -> last used modelId)
  modelPreferences: Partial<Record<PlaygroundMode, string>>;

  // History
  history: PlaygroundGeneration[];

  // Templates
  templates: PlaygroundTemplate[];

  // UI
  isGenerating: boolean;
  activeGenerationIds: string[];
  showAdvancedParams: boolean;
  showTemplateModal: boolean;
  showHistoryDrawer: boolean;

  // Template favorites (local, not persisted to backend)
  favoriteTemplateIds: string[];
  toggleTemplateFavorite: (id: string) => void;
  isTemplateFavorited: (id: string) => boolean;

  // Featured output per generation (best-of-batch); one per batch, localStorage-persisted
  featuredByGen: Record<string, string>;
  toggleFeatured: (genId: string, outputId: string) => void;
  isFeatured: (genId: string, outputId: string) => boolean;

  // Generation queue (client-side concurrency gate)
  queue: QueuedRequest[];
  maxConcurrent: number;
  enqueueRequest: (req: Omit<QueuedRequest, 'id' | 'status' | 'enqueuedAt'>) => void;
  markDispatching: (id: string) => void;
  removeFromQueue: (id: string) => void;
  setMaxConcurrent: (n: number) => void;

  // Actions — input setters
  setMode: (mode: PlaygroundMode) => void;
  setModelId: (modelId: string) => void;
  setPrompt: (prompt: string) => void;
  setNegativePrompt: (neg: string) => void;
  setInputMedia: (media: string[]) => void;
  /** Push a generated result back into the compose panel as reference input,
   *  switching an image to i2i (default) or i2v. Video-to-video is not
   *  supported by the approved New API catalog. */
  useResultAsReference: (
    mediaPath: string,
    mediaType: 'image' | 'video',
    targetMode?: PlaygroundMode,
  ) => void;
  setParameters: (params: Record<string, any>) => void;
  setBatchSize: (size: number) => void;
  setShowAdvancedParams: (show: boolean) => void;
  setShowTemplateModal: (show: boolean) => void;
  setShowHistoryDrawer: (show: boolean) => void;

  // Actions — generation lifecycle
  startGeneration: (gen: PlaygroundGeneration) => void;
  updateGeneration: (gen: PlaygroundGeneration) => void;
  removeGeneration: (id: string) => void;

  // Actions — history
  setHistory: (history: PlaygroundGeneration[]) => void;
  appendToHistory: (gen: PlaygroundGeneration) => void;

  // Actions — templates
  setTemplates: (templates: PlaygroundTemplate[]) => void;
  addTemplate: (template: PlaygroundTemplate) => void;
  updateTemplate: (template: PlaygroundTemplate) => void;
  removeTemplate: (id: string) => void;
  applyTemplate: (template: PlaygroundTemplate) => void;

  // Actions — reset
  resetInput: () => void;
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_MODE: PlaygroundMode = 't2i';
const DEFAULT_MODEL_ID = DEFAULT_ACTIVE_MODELS.image;
const DEFAULT_PROMPT = '';
const DEFAULT_BATCH_SIZE = 1;
const DEFAULT_PARAMETERS = getEffectivePlaygroundParameters(
  DEFAULT_MODE,
  DEFAULT_MODEL_ID,
  {},
);

function exposeUploadAfterCleanupFailure(path: string): void {
  const current = usePlaygroundStore.getState();
  if (current.inputMedia.includes(path)) return;

  const inputMedia = [...current.inputMedia, path];
  const needsImageEditMode = (
    current.mode === 't2i'
    || current.mode === 't2v'
    || (current.mode === 'i2v' && inputMedia.length > 1)
  );
  if (!needsImageEditMode) {
    usePlaygroundStore.setState({ inputMedia });
    return;
  }

  const mode: PlaygroundMode = 'i2i';
  const modelId = normalizeActiveModel('image', current.modelPreferences[mode]);
  usePlaygroundStore.setState({
    mode,
    modelId,
    inputMedia,
    parameters: getEffectivePlaygroundParameters(mode, modelId, current.parameters),
  });
}

function reclaimOwnedUploads(paths: string[], context: string): void {
  for (const path of paths) {
    void Promise.resolve()
      .then(() => playgroundApi.deleteUpload(path))
      .catch((error) => {
        console.error(`[Playground] Failed to reclaim ${context} upload:`, error);
        exposeUploadAfterCleanupFailure(path);
      });
  }
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const usePlaygroundStore = create<PlaygroundState>((set, get) => ({
  // -- Current input --------------------------------------------------------
  mode: DEFAULT_MODE,
  modelId: DEFAULT_MODEL_ID,
  prompt: DEFAULT_PROMPT,
  negativePrompt: '',
  inputMedia: [],
  parameters: DEFAULT_PARAMETERS,
  batchSize: DEFAULT_BATCH_SIZE,

  // -- Model preferences ----------------------------------------------------
  modelPreferences: loadModelPreferences(),

  // -- History ---------------------------------------------------------------
  history: [],

  // -- Templates -------------------------------------------------------------
  templates: [],

  // -- UI --------------------------------------------------------------------
  isGenerating: false,
  activeGenerationIds: [],
  showAdvancedParams: false,
  showTemplateModal: false,
  showHistoryDrawer: false,

  // -- Template favorites ----------------------------------------------------
  favoriteTemplateIds: [],
  toggleTemplateFavorite: (id) => {
    const { favoriteTemplateIds } = get();
    if (favoriteTemplateIds.includes(id)) {
      set({ favoriteTemplateIds: favoriteTemplateIds.filter((fid) => fid !== id) });
    } else {
      set({ favoriteTemplateIds: [...favoriteTemplateIds, id] });
    }
  },
  isTemplateFavorited: (id) => get().favoriteTemplateIds.includes(id),

  // -- Featured output (best-of-batch, one per generation) -------------------
  featuredByGen: loadFeatured(),
  toggleFeatured: (genId, outputId) => {
    const next = { ...get().featuredByGen };
    if (next[genId] === outputId) delete next[genId];
    else next[genId] = outputId;
    saveFeatured(next);
    set({ featuredByGen: next });
  },
  isFeatured: (genId, outputId) => get().featuredByGen[genId] === outputId,

  // -- Generation queue (client-side concurrency gate) -----------------------
  queue: [],
  maxConcurrent: loadConcurrency(),
  enqueueRequest: (req) =>
    set((s) => ({
      queue: [
        ...s.queue,
        { ...req, id: `q${++queueSeq}`, status: 'pending' as const, enqueuedAt: Date.now() },
      ],
    })),
  markDispatching: (id) =>
    set((s) => ({
      queue: s.queue.map((q) => (q.id === id ? { ...q, status: 'dispatching' as const } : q)),
    })),
  removeFromQueue: (id) => {
    const current = get();
    const removed = current.queue.find((request) => request.id === id);
    const queue = current.queue.filter((request) => request.id !== id);
    set({ queue });
    if (!removed) return;
    const protectedPaths = new Set([
      ...current.inputMedia,
      ...queue.flatMap((request) => request.inputMedia),
    ]);
    reclaimOwnedUploads(
      Array.from(new Set(
        removed.inputMedia.filter(
          (path) => isOwnedPlaygroundUpload(path) && !protectedPaths.has(path),
        ),
      )),
      'deferred queue',
    );
  },
  setMaxConcurrent: (n) => {
    const clamped = Math.max(1, Math.min(8, Math.round(n)));
    saveConcurrency(clamped);
    set({ maxConcurrent: clamped });
  },

  // =========================================================================
  // Actions
  // =========================================================================

  // -- Input setters ---------------------------------------------------------

  setMode: (mode) => {
    const { inputMedia, modelPreferences, parameters, queue } = get();
    const capability = mode === 't2i' || mode === 'i2i' ? 'image' : 'video';
    const preferredModel = normalizeActiveModel(capability, modelPreferences[mode]);
    const nextInputMedia = getEffectivePlaygroundInputMedia(mode, inputMedia);
    set({
      mode,
      modelId: preferredModel,
      inputMedia: nextInputMedia,
      parameters: getEffectivePlaygroundParameters(mode, preferredModel, parameters),
    });
    reclaimOwnedUploads(
      droppedOwnedUploads(inputMedia, nextInputMedia, queue),
      'mode-dropped',
    );
  },

  setModelId: (modelId) => {
    const { inputMedia, mode, modelPreferences, parameters, queue } = get();
    const capability = mode === 't2i' || mode === 'i2i' ? 'image' : 'video';
    if (!isApprovedModelForCapability(modelId, capability)) return;
    const nextPreferences = { ...modelPreferences, [mode]: modelId };
    saveModelPreferences(nextPreferences);
    const nextInputMedia = getEffectivePlaygroundInputMedia(mode, inputMedia, {
      allowTextToImageReferences: mode === 't2i',
    });
    set({
      modelId,
      modelPreferences: nextPreferences,
      inputMedia: nextInputMedia,
      parameters: getEffectivePlaygroundParameters(mode, modelId, parameters),
    });
    reclaimOwnedUploads(
      droppedOwnedUploads(inputMedia, nextInputMedia, queue),
      'model-dropped',
    );
  },

  setPrompt: (prompt) => set({ prompt }),

  setNegativePrompt: (negativePrompt) => set({ negativePrompt }),

  setInputMedia: (inputMedia) => set({ inputMedia }),

  useResultAsReference: (mediaPath, mediaType, targetMode) => {
    if (mediaType === 'video') return;
    const { inputMedia, modelPreferences, parameters, queue } = get();
    const mode: PlaygroundMode = targetMode ?? 'i2i';
    const capability = mode === 't2i' || mode === 'i2i' ? 'image' : 'video';
    const preferredModel = normalizeActiveModel(capability, modelPreferences[mode]);
    const nextInputMedia = getEffectivePlaygroundInputMedia(mode, [mediaPath]);
    set({
      mode,
      inputMedia: nextInputMedia,
      modelId: preferredModel,
      parameters: getEffectivePlaygroundParameters(mode, preferredModel, parameters),
    });
    reclaimOwnedUploads(
      droppedOwnedUploads(inputMedia, nextInputMedia, queue),
      'replaced',
    );
  },

  setParameters: (parameters) => set({ parameters }),

  setBatchSize: (batchSize) => set({ batchSize }),

  setShowAdvancedParams: (showAdvancedParams) => set({ showAdvancedParams }),

  setShowTemplateModal: (showTemplateModal) =>
    set(showTemplateModal ? { showTemplateModal, showHistoryDrawer: false } : { showTemplateModal }),

  setShowHistoryDrawer: (showHistoryDrawer) =>
    set(showHistoryDrawer ? { showHistoryDrawer, showTemplateModal: false } : { showHistoryDrawer }),

  // -- Generation lifecycle --------------------------------------------------

  startGeneration: (gen) => {
    const { activeGenerationIds, history } = get();
    set({
      activeGenerationIds: activeGenerationIds.includes(gen.id)
        ? activeGenerationIds
        : [...activeGenerationIds, gen.id],
      history: [gen, ...history.filter((item) => item.id !== gen.id)],
      isGenerating: true,
    });
  },

  updateGeneration: (gen) => {
    const { history, activeGenerationIds } = get();
    const updatedHistory = history.map((h) => (h.id === gen.id ? gen : h));
    const isTerminal = gen.status === 'completed' || gen.status === 'failed';
    const updatedActive = isTerminal
      ? activeGenerationIds.filter((id) => id !== gen.id)
      : activeGenerationIds;

    set({
      history: updatedHistory,
      activeGenerationIds: updatedActive,
      isGenerating: updatedActive.length > 0,
    });
  },

  removeGeneration: (id) => {
    const { history, activeGenerationIds, featuredByGen } = get();
    const updatedActive = activeGenerationIds.filter((gid) => gid !== id);
    const updatedFeatured = { ...featuredByGen };
    delete updatedFeatured[id];
    set({
      history: history.filter((h) => h.id !== id),
      activeGenerationIds: updatedActive,
      isGenerating: updatedActive.length > 0,
      featuredByGen: updatedFeatured,
    });
  },

  // -- History ---------------------------------------------------------------

  setHistory: (history) => {
    const activeGenerationIds = history
      .filter((generation) => generation.status === 'pending' || generation.status === 'processing')
      .map((generation) => generation.id);
    set({
      history,
      activeGenerationIds,
      isGenerating: activeGenerationIds.length > 0,
    });
  },

  appendToHistory: (gen) => set((s) => ({ history: [gen, ...s.history] })),

  // -- Templates -------------------------------------------------------------

  setTemplates: (templates) => set({ templates }),

  addTemplate: (template) =>
    set((s) => ({ templates: [...s.templates, template] })),

  updateTemplate: (template) =>
    set((s) => ({
      templates: s.templates.map((t) => (t.id === template.id ? template : t)),
    })),

  removeTemplate: (id) =>
    set((s) => ({ templates: s.templates.filter((t) => t.id !== id) })),

  applyTemplate: (template) => {
    const current = get();
    const nextMode = template.default_mode ?? current.mode;
    const capability = nextMode === 't2i' || nextMode === 'i2i' ? 'image' : 'video';
    const nextModel = normalizeActiveModel(
      capability,
      template.default_model_id ?? (
        nextMode === current.mode ? current.modelId : current.modelPreferences[nextMode]
      ),
    );
    const hasTemplateParameters = (
      template.default_parameters != null
      && Object.keys(template.default_parameters).length > 0
    );
    const parameterSource = hasTemplateParameters
      ? template.default_parameters
      : nextMode === current.mode && nextModel === current.modelId
        ? current.parameters
        : {};
    const nextInputMedia = getEffectivePlaygroundInputMedia(nextMode, current.inputMedia, {
      allowTextToImageReferences: (
        nextMode === 't2i'
        && nextMode === current.mode
      ),
    });
    const patch: Partial<PlaygroundState> = {
      prompt: template.prompt,
      mode: nextMode,
      modelId: nextModel,
      inputMedia: nextInputMedia,
      parameters: getEffectivePlaygroundParameters(nextMode, nextModel, parameterSource),
    };
    if (template.negative_prompt != null) {
      patch.negativePrompt = template.negative_prompt;
    }
    set(patch);
    reclaimOwnedUploads(
      droppedOwnedUploads(current.inputMedia, nextInputMedia, current.queue),
      'template-dropped',
    );
  },

  // -- Reset -----------------------------------------------------------------

  resetInput: () => {
    const { inputMedia: previousInputMedia, queue } = get();
    set({
      prompt: DEFAULT_PROMPT,
      negativePrompt: '',
      inputMedia: [],
      parameters: DEFAULT_PARAMETERS,
      batchSize: DEFAULT_BATCH_SIZE,
    });
    reclaimOwnedUploads(
      droppedOwnedUploads(previousInputMedia, [], queue),
      'reset',
    );
  },
}));

/**
 * Drop all module-level Playground state when the authenticated workspace
 * changes. The backend remains authoritative for history/templates; only the
 * new workspace's harmless UI preferences are reloaded from its scoped cache.
 */
export function resetPlaygroundWorkspaceState(): void {
  const modelPreferences = loadModelPreferences();
  usePlaygroundStore.setState({
    mode: DEFAULT_MODE,
    modelId: normalizeActiveModel('image', modelPreferences[DEFAULT_MODE]),
    prompt: DEFAULT_PROMPT,
    negativePrompt: '',
    inputMedia: [],
    parameters: DEFAULT_PARAMETERS,
    batchSize: DEFAULT_BATCH_SIZE,
    modelPreferences,
    history: [],
    templates: [],
    isGenerating: false,
    activeGenerationIds: [],
    showAdvancedParams: false,
    showTemplateModal: false,
    showHistoryDrawer: false,
    favoriteTemplateIds: [],
    featuredByGen: loadFeatured(),
    queue: [],
    maxConcurrent: loadConcurrency(),
  });
}
