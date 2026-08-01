'use client';

import { useEffect, useCallback, useRef } from 'react';
import { useTranslations } from 'next-intl';
import { Sparkles } from 'lucide-react';
import ModeSelector from './ModeSelector';
import ModelSelector from './ModelSelector';
import MediaInput from './MediaInput';
import PromptInput from './PromptInput';
import ParameterBar from './ParameterBar';
import ResultGallery from './ResultGallery';
import { usePlaygroundStore, type PlaygroundMode, type PlaygroundGeneration, type QueuedRequest } from './usePlaygroundStore';
import {
  getEffectivePlaygroundInputMedia,
  getEffectivePlaygroundParameters,
  supportsPlaygroundNegativePrompt,
} from './playgroundModels';
import { playgroundApi, type PlaygroundGenerationResponse } from '@/lib/api';
import GlobalPageHeader from '@/components/layout/GlobalPageHeader';
import { toast } from '@/store/toastStore';
import { waitForPlaygroundGeneration } from './pollGeneration';

/** Modes that require media input (image or video source).
 *  t2i also shows optional media input — when provided, it auto-becomes i2i. */
const MODES_WITH_MEDIA: PlaygroundMode[] = ['i2i', 'i2v'];
const MODES_WITH_OPTIONAL_MEDIA: PlaygroundMode[] = ['t2i'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert API response to store-compatible PlaygroundGeneration */
function toGeneration(resp: PlaygroundGenerationResponse): PlaygroundGeneration {
  return {
    id: resp.id,
    mode: resp.mode as PlaygroundMode,
    model_id: resp.model_id,
    prompt: resp.prompt,
    negative_prompt: resp.negative_prompt,
    input_media: resp.input_media,
    parameters: resp.parameters,
    batch_size: resp.batch_size,
    outputs: resp.outputs.map((o) => ({
      id: o.id,
      media_path: o.media_path,
      media_type: o.media_type as 'image' | 'video',
      thumbnail_path: o.thumbnail_path,
      saved_to_library: o.saved_to_library,
      library_category: o.library_category,
    })),
    status: resp.status as PlaygroundGeneration['status'],
    error: resp.error,
    error_code: resp.error_code,
    error_diagnostic: resp.error_diagnostic,
    provider_name: resp.provider_name,
    provider_task_id: resp.provider_task_id,
    provider_request_id: resp.provider_request_id,
    created_at: resp.created_at,
    updated_at: resp.updated_at,
    finished_at: resp.finished_at,
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function PlaygroundPage() {
  const t = useTranslations('playground');

  const mode = usePlaygroundStore((s) => s.mode);
  const modelId = usePlaygroundStore((s) => s.modelId);
  const prompt = usePlaygroundStore((s) => s.prompt);
  const negativePrompt = usePlaygroundStore((s) => s.negativePrompt);
  const inputMedia = usePlaygroundStore((s) => s.inputMedia);
  const parameters = usePlaygroundStore((s) => s.parameters);
  const batchSize = usePlaygroundStore((s) => s.batchSize);
  const setHistory = usePlaygroundStore((s) => s.setHistory);
  const setTemplates = usePlaygroundStore((s) => s.setTemplates);
  const startGeneration = usePlaygroundStore((s) => s.startGeneration);
  const updateGeneration = usePlaygroundStore((s) => s.updateGeneration);
  const enqueueRequest = usePlaygroundStore((s) => s.enqueueRequest);
  const markDispatching = usePlaygroundStore((s) => s.markDispatching);
  const removeFromQueue = usePlaygroundStore((s) => s.removeFromQueue);
  const queue = usePlaygroundStore((s) => s.queue);
  const activeCount = usePlaygroundStore((s) => s.activeGenerationIds.length);
  const maxConcurrent = usePlaygroundStore((s) => s.maxConcurrent);

  const pollControllers = useRef<Map<string, AbortController>>(new Map());

  // ─── Cleanup poll timers ───────────────────────────────────────────────────

  useEffect(() => {
    const controllers = pollControllers.current;
    return () => {
      controllers.forEach((controller) => controller.abort());
      controllers.clear();
    };
  }, []);

  // ─── Status poller ─────────────────────────────────────────────────────────

  const startPolling = useCallback((generationId: string) => {
    if (pollControllers.current.has(generationId)) return;
    const controller = new AbortController();
    pollControllers.current.set(generationId, controller);

    void waitForPlaygroundGeneration(generationId, {
      signal: controller.signal,
    }).then((fullResponse) => {
      if (!controller.signal.aborted) {
        updateGeneration(toGeneration(fullResponse));
      }
    }).catch((error) => {
      if (!controller.signal.aborted) {
        console.error('[Playground] Poll failed for', generationId, error);
      }
    }).finally(() => {
      if (pollControllers.current.get(generationId) === controller) {
        pollControllers.current.delete(generationId);
      }
    });
  }, [updateGeneration]);

  // ─── Fetch initial data and resume server-side work ────────────────────────

  useEffect(() => {
    let cancelled = false;

    playgroundApi.getHistory().then((items) => {
      if (cancelled) return;
      const generations = items.map(toGeneration);
      setHistory(generations);
      for (const generation of generations) {
        if (generation.status === 'pending' || generation.status === 'processing') {
          startPolling(generation.id);
        }
      }
    }).catch((err) => {
      console.error('[Playground] Failed to fetch history:', err);
    });

    playgroundApi.getTemplates().then((items) => {
      if (cancelled) return;
      setTemplates(
        items.map((t) => ({
          id: t.id,
          name: t.name,
          category: t.category,
          prompt: t.prompt,
          negative_prompt: t.negative_prompt,
          default_mode: t.default_mode as PlaygroundMode | undefined,
          default_model_id: t.default_model_id,
          default_parameters: t.default_parameters,
          created_at: t.created_at,
          updated_at: t.updated_at,
        }))
      );
    }).catch((err) => {
      console.error('[Playground] Failed to fetch templates:', err);
    });

    return () => {
      cancelled = true;
    };
  }, [setHistory, setTemplates, startPolling]);

  // ─── Generate handler — enqueue a request; the dispatcher runs it ──────────

  const handleGenerate = useCallback(() => {
    const usableInputCount = inputMedia.filter((item) => item.trim().length > 0).length;
    if (
      !prompt.trim()
      || (mode === 'i2i' && usableInputCount < 1)
      || (mode === 'i2v' && usableInputCount !== 1)
    ) {
      return;
    }
    // Auto-detect i2i: t2i + reference images -> i2i
    const effectiveMode = (mode === 't2i' && inputMedia.length > 0) ? 'i2i' : mode;
    const effectiveParameters = getEffectivePlaygroundParameters(
      effectiveMode,
      modelId,
      parameters,
    );
    const effectiveInputMedia = getEffectivePlaygroundInputMedia(
      effectiveMode,
      inputMedia,
    );
    enqueueRequest({
      mode: effectiveMode,
      modelId,
      prompt: prompt.trim(),
      negativePrompt: supportsPlaygroundNegativePrompt(effectiveMode, modelId)
        ? negativePrompt || undefined
        : undefined,
      inputMedia: effectiveInputMedia,
      parameters: effectiveParameters,
      batchSize,
    });
  }, [mode, modelId, prompt, negativePrompt, inputMedia, parameters, batchSize, enqueueRequest]);

  // ─── Queue dispatcher — POST a queued request, then poll for status ────────

  const dispatchRequest = useCallback(async (req: QueuedRequest) => {
    try {
      const requestInputMedia = getEffectivePlaygroundInputMedia(req.mode, req.inputMedia);
      const requestParameters = getEffectivePlaygroundParameters(
        req.mode,
        req.modelId,
        req.parameters,
      );
      const resp = await playgroundApi.generate({
        mode: req.mode,
        model_id: req.modelId,
        prompt: req.prompt,
        negative_prompt: supportsPlaygroundNegativePrompt(req.mode, req.modelId)
          ? req.negativePrompt || undefined
          : undefined,
        input_media: requestInputMedia.length > 0 ? requestInputMedia : undefined,
        parameters: requestParameters,
        batch_size: req.batchSize > 1 ? req.batchSize : undefined,
      });
      const gen = toGeneration(resp);
      startGeneration(gen);
      removeFromQueue(req.id);
      if (gen.status !== 'completed' && gen.status !== 'failed') {
        startPolling(gen.id);
      }
    } catch (err) {
      console.error('[Playground] Dispatch failed:', err);
      toast.error(t('compose.dispatchFailed'));
      removeFromQueue(req.id);
    }
  }, [removeFromQueue, startGeneration, startPolling, t]);

  // Pump: dispatch pending requests up to the concurrency limit.
  const pump = useCallback(() => {
    const s = usePlaygroundStore.getState();
    const dispatching = s.queue.filter((q) => q.status === 'dispatching').length;
    let slots = s.maxConcurrent - s.activeGenerationIds.length - dispatching;
    if (slots <= 0) return;
    for (const req of s.queue) {
      if (slots <= 0) break;
      if (req.status !== 'pending') continue;
      slots -= 1;
      markDispatching(req.id);
      dispatchRequest(req);
    }
  }, [markDispatching, dispatchRequest]);

  // Run the pump whenever the queue, in-flight count, or concurrency changes.
  useEffect(() => {
    pump();
  }, [queue, activeCount, maxConcurrent, pump]);

  // ─── Derived values ────────────────────────────────────────────────────────

  const showMediaInput = MODES_WITH_MEDIA.includes(mode) || MODES_WITH_OPTIONAL_MEDIA.includes(mode);
  const usableInputCount = inputMedia.filter((item) => item.trim().length > 0).length;
  let generateBlockReason: string | null = null;
  if (!prompt.trim()) {
    generateBlockReason = t('compose.promptRequired');
  } else if (mode === 'i2i' && usableInputCount < 1) {
    generateBlockReason = t('compose.referenceRequired');
  } else if (mode === 'i2v' && usableInputCount !== 1) {
    generateBlockReason = t('compose.firstFrameRequired');
  }
  const canGenerate = generateBlockReason === null;

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full flex-col overflow-hidden text-foreground">
      <GlobalPageHeader
        title={t('header.title')}
        actions={(
          <span className="atelier-badge rounded border border-glass-border bg-glass px-2 py-1 text-[0.625rem] uppercase tracking-[0.18em] text-text-muted">
            {t(`mode.${mode}`)}
          </span>
        )}
      />

      {/* ═══ SPLIT LAYOUT ═══ */}
      <div
        data-testid="playground-split-layout"
        className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-hidden"
      >
        {/* ─── LEFT: INPUT PANEL ─── */}
        <aside
          data-testid="playground-input-panel"
          className="flex w-full shrink-0 flex-col overflow-visible border-b border-glass-border md:h-full md:w-[420px] md:overflow-hidden md:border-b-0 md:border-r"
        >
          <div
            data-testid="playground-composer-scroll"
            className="flex flex-col gap-3 px-4 pb-3 pt-4 scrollbar-thin md:min-h-0 md:flex-1 md:overflow-y-auto md:pb-4"
          >
            {/* Mode */}
            <section className="glass-panel atelier-card rounded-[20px] px-5 py-5">
              <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
                {t('compose.modeLabel')}
              </div>
              <ModeSelector />
            </section>

            {/* Prompt — first, the primary input */}
            <section className="glass-panel atelier-card rounded-[20px] px-5 py-5">
              <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
                {t('compose.promptLabel')}
              </div>
              <PromptInput />
            </section>

            {/* Media Input (conditional) */}
            {showMediaInput && (
              <section className="glass-panel atelier-card rounded-[20px] px-5 py-5">
                <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
                  {t(mode === 'i2v' ? 'compose.mediaFirstFrame' : 'compose.mediaReference')}
                </div>
                <MediaInput />
              </section>
            )}

            {/* Model & Parameters — merged into one card (mockup) */}
            <section className="glass-panel atelier-card rounded-[20px] px-5 py-5">
              <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
                {t('compose.modelLabel')}
              </div>
              <ModelSelector />
              <div className="my-4 h-px bg-border-subtle" />
              <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
                {t('compose.parametersLabel')}
              </div>
              <ParameterBar />
            </section>

            {/* Spacer keeps short forms visually balanced without covering controls. */}
            <div className="flex-1" />
          </div>

          {/* The CTA owns a separate layout row, so scrolled cards cannot cover it. */}
          <div
            data-testid="playground-generate-bar"
            className="shrink-0 border-t border-glass-border bg-background/95 px-4 pb-4 pt-3 backdrop-blur-md"
          >
            {generateBlockReason && (
              <p
                id="playground-generate-block-reason"
                className="mb-2 text-center text-[0.6875rem] text-text-muted"
              >
                {generateBlockReason}
              </p>
            )}
            <button
              type="button"
              onClick={handleGenerate}
              disabled={!canGenerate}
              aria-describedby={generateBlockReason ? 'playground-generate-block-reason' : undefined}
              title={generateBlockReason ?? undefined}
              className={[
                'inline-flex w-full items-center justify-center gap-[7px] rounded-full px-6 py-[13px]',
                "font-['Space_Grotesk',sans-serif] text-sm font-semibold",
                'bg-primary text-on-accent shadow-[var(--glow-primary)] transition-all duration-150 disabled:opacity-40 disabled:shadow-none',
                canGenerate
                  ? 'hover:bg-primary-hover hover:-translate-y-px cursor-pointer'
                  : 'cursor-not-allowed',
              ].join(' ')}
            >
              <Sparkles size={16} aria-hidden="true" />
              <span>
                {batchSize > 1
                  ? t('compose.generateBatch', { count: batchSize })
                  : t('compose.generate')}
              </span>
            </button>
          </div>
        </aside>

        {/* ─── RIGHT: RESULT GALLERY ─── */}
        <main
          data-testid="playground-results-panel"
          className="flex min-h-[360px] w-full min-w-0 flex-1 flex-col overflow-hidden md:min-h-0"
        >
          <ResultGallery />
        </main>
      </div>
    </div>
  );
}
