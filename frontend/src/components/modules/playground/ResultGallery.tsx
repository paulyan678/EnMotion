'use client';

import { useState, useMemo, useCallback } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { Sparkles, Grid3x3, GalleryHorizontal } from 'lucide-react';
import { usePlaygroundStore, type PlaygroundGeneration } from './usePlaygroundStore';
import { playgroundApi } from '@/lib/api';
import {
  getEffectivePlaygroundInputMedia,
  getEffectivePlaygroundParameters,
  supportsPlaygroundNegativePrompt,
} from './playgroundModels';
import ResultCard from './ResultCard';
import GalleryView from './GalleryView';
import DetailPanel from './DetailPanel';
import QueuePanel from './QueuePanel';
import {
  apiTimestampMilliseconds,
  appCalendarDateKey,
  appDateTimeFormatter,
  parseApiTimestamp,
} from '@/lib/dateTime';

type FilterType = 'all' | 'image' | 'video';

const VIDEO_MODES = new Set(['t2v', 'i2v']);

function formatSessionLabel(
  dateStr: string,
  todayLabel: string,
  yesterdayLabel: string,
  locale: string,
  now = new Date(),
): string {
  const date = parseApiTimestamp(dateStr);
  if (!date) return '—';
  const time = appDateTimeFormatter(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date);
  const itemDay = appCalendarDateKey(date);

  if (itemDay === appCalendarDateKey(now)) {
    return `${todayLabel} · ${time}`;
  }
  if (itemDay === appCalendarDateKey(new Date(now.getTime() - 86400000))) {
    return `${yesterdayLabel} · ${time}`;
  }
  const day = appDateTimeFormatter(locale, {
    month: 'numeric',
    day: 'numeric',
  }).format(date);
  return `${day} · ${time}`;
}

export default function ResultGallery() {
  const {
    history,
    startGeneration,
    updateGeneration,
    useResultAsReference: setResultAsReference,
  } = usePlaygroundStore();
  const t = useTranslations('playground');
  const tui = useTranslations('ui.playground');
  const locale = useLocale();
  const [activeFilter, setActiveFilter] = useState<FilterType>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'gallery'>('grid');
  const [detailGen, setDetailGen] = useState<PlaygroundGeneration | null>(null);
  const [detailOutputId, setDetailOutputId] = useState<string | undefined>(undefined);

  const handleOpenDetail = useCallback((gen: PlaygroundGeneration, outputId?: string) => {
    setDetailGen(gen);
    setDetailOutputId(outputId);
  }, []);

  const handleRetry = useCallback(async (gen: PlaygroundGeneration) => {
    try {
      const inputMedia = getEffectivePlaygroundInputMedia(gen.mode, gen.input_media);
      const parameters = getEffectivePlaygroundParameters(
        gen.mode,
        gen.model_id,
        gen.parameters,
      );
      const resp = await playgroundApi.generate({
        mode: gen.mode,
        model_id: gen.model_id,
        prompt: gen.prompt,
        negative_prompt: supportsPlaygroundNegativePrompt(gen.mode, gen.model_id)
          ? gen.negative_prompt || undefined
          : undefined,
        input_media: inputMedia.length > 0 ? inputMedia : undefined,
        parameters,
        batch_size: gen.batch_size > 1 ? gen.batch_size : undefined,
      });
      const newGen: PlaygroundGeneration = {
        id: resp.id,
        mode: resp.mode as PlaygroundGeneration['mode'],
        model_id: resp.model_id,
        prompt: resp.prompt,
        negative_prompt: resp.negative_prompt,
        input_media: resp.input_media,
        parameters: resp.parameters,
        batch_size: resp.batch_size,
        outputs: [],
        status: resp.status as PlaygroundGeneration['status'],
        error: resp.error,
        created_at: resp.created_at,
        updated_at: resp.updated_at,
        finished_at: resp.finished_at,
      };
      startGeneration(newGen);
      // Poll for status
      const poll = setInterval(async () => {
        try {
          const s = await playgroundApi.getGenerationStatus(newGen.id);
          if (s.status === 'completed' || s.status === 'failed') {
            clearInterval(poll);
            const full = await playgroundApi.getGeneration(newGen.id);
            updateGeneration({
              ...newGen,
              status: full.status as PlaygroundGeneration['status'],
              outputs: full.outputs.map((o) => ({
                id: o.id,
                media_path: o.media_path,
                media_type: o.media_type as 'image' | 'video',
                thumbnail_path: o.thumbnail_path,
                saved_to_library: o.saved_to_library,
                library_category: o.library_category,
              })),
              error: full.error,
              updated_at: full.updated_at,
              finished_at: full.finished_at,
            });
          }
        } catch { clearInterval(poll); }
      }, 2000);
    } catch (err) {
      console.error('[Playground] Retry failed:', err);
    }
  }, [startGeneration, updateGeneration]);

  const handleDelete = useCallback(async (gen: PlaygroundGeneration) => {
    try {
      await playgroundApi.deleteGeneration(gen.id);
      usePlaygroundStore.getState().removeGeneration(gen.id);
    } catch (err) {
      console.error('[Playground] Delete failed:', err);
    }
  }, []);

  // Image result → "Generate video": set the image as i2v reference and switch mode.
  const handleGenerateVideo = useCallback(
    (mediaPath: string) => setResultAsReference(mediaPath, 'image', 'i2v'),
    [setResultAsReference],
  );

  const filtered = useMemo(() => {
    if (activeFilter === 'all') return history;
    if (activeFilter === 'image') {
      return history.filter((g) => !VIDEO_MODES.has(g.mode));
    }
    return history.filter((g) => VIDEO_MODES.has(g.mode));
  }, [history, activeFilter]);

  // Sort descending by created_at
  const sorted = useMemo(
    () =>
      [...filtered].sort(
        (a, b) =>
          (apiTimestampMilliseconds(b.created_at) ?? 0) -
          (apiTimestampMilliseconds(a.created_at) ?? 0),
      ),
    [filtered],
  );

  // Build items with session dividers
  const itemsWithDividers = useMemo(() => {
    const result: Array<
      | { type: 'generation'; data: PlaygroundGeneration }
      | { type: 'divider'; label: string; key: string }
    > = [];

    for (let i = 0; i < sorted.length; i++) {
      if (i > 0) {
        const prevTime = apiTimestampMilliseconds(sorted[i - 1].created_at) ?? 0;
        const currTime = apiTimestampMilliseconds(sorted[i].created_at) ?? 0;
        const gap = prevTime - currTime; // prev is more recent (descending)
        if (gap > 30 * 60 * 1000) {
          result.push({
            type: 'divider',
            label: formatSessionLabel(
              sorted[i].created_at,
              t('results.today'),
              t('results.yesterday'),
              locale,
            ),
            key: `divider-${sorted[i].id}`,
          });
        }
      }
      result.push({ type: 'generation', data: sorted[i] });
    }

    return result;
  }, [locale, sorted, t]);

  // Flat list of generation data items (no dividers) for GalleryView and DetailPanel
  const dataItems = useMemo(
    () =>
      itemsWithDividers
        .filter((item): item is { type: 'generation'; data: PlaygroundGeneration } => item.type === 'generation')
        .map((item) => item.data),
    [itemsWithDividers],
  );

  // Grid items: expand each completed generation into one tile per output (so
  // multi-output batches show all N); keep pending/processing/failed as one card.
  const gridItems = useMemo(() => {
    const out: Array<
      | { kind: 'divider'; label: string; key: string }
      | { kind: 'output'; gen: PlaygroundGeneration; outputIndex: number }
      | { kind: 'gen'; gen: PlaygroundGeneration }
    > = [];
    for (const item of itemsWithDividers) {
      if (item.type === 'divider') {
        out.push({ kind: 'divider', label: item.label, key: item.key });
        continue;
      }
      const g = item.data;
      if (g.status === 'completed' && g.outputs.length > 0) {
        g.outputs.forEach((_, i) => out.push({ kind: 'output', gen: g, outputIndex: i }));
      } else {
        out.push({ kind: 'gen', gen: g });
      }
    }
    return out;
  }, [itemsWithDividers]);

  const filters: { key: FilterType; label: string }[] = [
    { key: 'all', label: t('results.filterAll') },
    { key: 'image', label: t('results.filterImage') },
    { key: 'video', label: t('results.filterVideo') },
  ];

  if (history.length === 0) {
    return (
      <div className="flex flex-col flex-1 overflow-hidden min-w-0 items-center justify-center">
        <Sparkles className="w-12 h-12 text-text-muted opacity-40 mb-4" />
        <p className="font-display atelier-display text-base text-foreground mb-1">
          {t('results.emptyTitle')}
        </p>
        <p className="text-xs text-text-muted">{t('results.emptyBody')}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden min-w-0">
      {/* Header */}
      <div className="px-7 py-4 flex items-center justify-between border-b border-border-subtle shrink-0">
        <div className="flex flex-col gap-1">
          <span className="font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-text-muted">
            {tui('results')}
          </span>
          <div className="flex items-center gap-2">
            <span className="text-[2.125rem] leading-[1.1] font-semibold tracking-[-0.02em] text-foreground font-display atelier-display">
              {t('results.title')}
            </span>
            <span className="font-mono text-[0.625rem] bg-elevated text-text-secondary rounded px-[6px] py-[1px]">
              {filtered.reduce((n, g) => n + g.outputs.length, 0)}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-[2px] bg-surface-inset rounded-full p-1 atelier-pill-tabs">
            {filters.map((f) => (
              <button
                key={f.key}
                onClick={() => setActiveFilter(f.key)}
                className={`rounded-full px-4 py-2 text-[0.8125rem] font-medium text-center transition-all cursor-pointer ${
                  activeFilter === f.key
                    ? 'bg-surface text-foreground atelier-pill-tab-active'
                    : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-[2px] bg-surface-inset rounded-full p-1 atelier-pill-tabs">
            <button
              onClick={() => setViewMode('grid')}
              aria-label={t('results.gridView')}
              aria-pressed={viewMode === 'grid'}
              className={`rounded-full p-2 transition-all cursor-pointer ${
                viewMode === 'grid'
                  ? 'bg-surface text-foreground atelier-pill-tab-active'
                  : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
              }`}
              title={t('results.gridView')}
            >
              <Grid3x3 className="w-4 h-4" />
            </button>
            <button
              onClick={() => setViewMode('gallery')}
              aria-label={t('results.galleryView')}
              aria-pressed={viewMode === 'gallery'}
              className={`rounded-full p-2 transition-all cursor-pointer ${
                viewMode === 'gallery'
                  ? 'bg-surface text-foreground atelier-pill-tab-active'
                  : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
              }`}
              title={t('results.galleryView')}
            >
              <GalleryHorizontal className="w-4 h-4" />
            </button>
          </div>

          <QueuePanel />
        </div>
      </div>

      {/* Content area */}
      {viewMode === 'gallery' ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <GalleryView
            generations={dataItems}
            onOpenDetail={handleOpenDetail}
            onRetry={handleRetry}
          />
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto p-6">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4 content-start">
            {gridItems.map((it) => {
              if (it.kind === 'divider') {
                return (
                  <div
                    key={it.key}
                    className="col-span-full flex items-center gap-3 py-2"
                  >
                    <div className="flex-1 h-px bg-border-subtle" />
                    <span className="font-mono text-[0.5625rem] text-text-muted uppercase tracking-wider whitespace-nowrap">
                      {it.label}
                    </span>
                    <div className="flex-1 h-px bg-border-subtle" />
                  </div>
                );
              }
              if (it.kind === 'output') {
                return (
                  <ResultCard
                    key={`${it.gen.id}-${it.outputIndex}`}
                    generation={it.gen}
                    outputIndex={it.outputIndex}
                    onRetry={handleRetry}
                    onDelete={handleDelete}
                    onGenerateVideo={handleGenerateVideo}
                    onOpenDetail={handleOpenDetail}
                  />
                );
              }
              return (
                <ResultCard
                  key={it.gen.id}
                  generation={it.gen}
                  onRetry={handleRetry}
                  onDelete={handleDelete}
                  onGenerateVideo={handleGenerateVideo}
                  onOpenDetail={handleOpenDetail}
                />
              );
            })}
          </div>
        </div>
      )}

      {/* Detail Panel */}
      {detailGen && (
        <DetailPanel
          generation={detailGen}
          allGenerations={dataItems}
          focusOutputId={detailOutputId}
          onClose={() => { setDetailGen(null); setDetailOutputId(undefined); }}
          onNavigate={(g) => handleOpenDetail(g)}
          onRetry={handleRetry}
          onGenerateVideo={handleGenerateVideo}
        />
      )}
    </div>
  );
}
