'use client';

import { useState, useCallback } from 'react';
import { Download, Video, Copy, Check, Replace, Crown, Bookmark } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { playgroundApi } from '@/lib/api';
import { apiFetch } from '@/lib/httpClient';
import { getAssetUrl } from '@/lib/utils';
import PreviewImage from '@/components/shared/preview/PreviewImage';
import { usePlaygroundStore, type PlaygroundGeneration } from './usePlaygroundStore';
import { useModelDisplayName } from '@/lib/useModelDisplayName';

interface ResultCardProps {
  generation: PlaygroundGeneration;
  outputIndex?: number;
  onGenerateVideo?: (imagePath: string) => void;
  onRetry?: (generation: PlaygroundGeneration) => void;
  onOpenDetail?: (generation: PlaygroundGeneration, outputId?: string) => void;
  onDelete?: (generation: PlaygroundGeneration) => void;
}

function formatTime(dateStr: string): string {
  const date = new Date(dateStr);
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

function getElapsedProgress(createdAt: string): number {
  const elapsed = Date.now() - new Date(createdAt).getTime();
  // Estimate ~60s for generation, cap at 90%
  const progress = Math.min(elapsed / 60000, 0.9);
  return progress * 100;
}

function FailedCard({ generation, onRetry, onDelete }: { generation: PlaygroundGeneration; onRetry?: (g: PlaygroundGeneration) => void; onDelete?: (g: PlaygroundGeneration) => void }) {
  const { prompt, model_id, created_at, error } = generation;
  const t = useTranslations('playground');
  const modelDisplayName = useModelDisplayName();
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const visibleError = error && !/[A-Za-z]{2,}/.test(error) ? error : t('card.failed');

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(visibleError).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="rounded-[20px] border border-status-failed-border bg-glass overflow-hidden">
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-label={expanded ? t('card.collapse') : t('card.expand')}
        className="relative overflow-hidden bg-elevated flex flex-col items-center justify-center cursor-pointer"
        style={{ aspectRatio: expanded ? undefined : '16/9', minHeight: expanded ? 120 : undefined }}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(event) => {
          if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            setExpanded((value) => !value);
          }
        }}
      >
        <div className="absolute inset-0 bg-status-failed-bg" />
        <div className="relative text-center px-4 py-3 w-full">
          <p className="font-mono text-[0.625rem] text-status-failed-fg uppercase mb-2">{t('card.failed')}</p>
          <p className={`text-[0.625rem] text-text-muted leading-relaxed break-all ${expanded ? '' : 'line-clamp-2'}`}>
            {visibleError}
          </p>
        </div>

        {/* Action bar */}
        <div className="relative flex items-center gap-2 pb-2">
          {onRetry && (
            <button
              onClick={(e) => { e.stopPropagation(); onRetry(generation); }}
              className="inline-flex min-h-9 items-center gap-1 rounded bg-primary/10 px-2.5 text-[0.6875rem] font-medium text-primary transition-colors hover:bg-primary/20"
            >
              ↻ {t('card.retry')}
            </button>
          )}
          {onDelete && (
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(generation); }}
              className="inline-flex min-h-9 items-center gap-1 rounded px-2.5 text-[0.6875rem] font-medium text-status-failed-fg/60 transition-colors hover:bg-status-failed-bg hover:text-status-failed-fg"
            >
              × {t('card.delete')}
            </button>
          )}
          <button
            onClick={handleCopy}
            className="inline-flex min-h-9 items-center gap-1 rounded px-2.5 text-[0.6875rem] font-medium text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
          >
            {copied ? <Check className="w-3 h-3 text-primary" /> : <Copy className="w-3 h-3" />}
            {copied ? t('card.copied') : t('card.copyError')}
          </button>
          <span className="text-[0.5625rem] text-text-muted ml-auto">
            {expanded ? t('card.collapse') : t('card.expand')}
          </span>
        </div>
      </div>

      <div className="px-3 py-[10px]">
        <p className="text-[0.6875rem] text-text-secondary line-clamp-2 mb-1.5">{prompt}</p>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[0.5625rem] bg-glass text-text-muted rounded px-[6px] py-[2px]">
            {modelDisplayName(model_id)}
          </span>
          <span className="font-mono text-[0.5625rem] text-text-muted">
            {formatTime(created_at)}
          </span>
        </div>
      </div>
    </div>
  );
}

function CompletedCard({ generation, outputIndex, onGenerateVideo, onOpenDetail }: { generation: PlaygroundGeneration; outputIndex: number; onGenerateVideo?: (path: string) => void; onOpenDetail?: (generation: PlaygroundGeneration, outputId?: string) => void }) {
  const { prompt, model_id, mode, outputs, created_at } = generation;
  const t = useTranslations('playground');
  const modelDisplayName = useModelDisplayName();
  const output = outputs[outputIndex];
  const isVideo = output?.media_type === 'video' || ['t2v', 'i2v'].includes(mode);
  const [saving, setSaving] = useState(false);

  const saved = output?.saved_to_library ?? false;
  const mediaUrl = output?.media_path ? getAssetUrl(output.media_path) : null;
  const thumbnailUrl = output?.thumbnail_path
    ? getAssetUrl(output.thumbnail_path)
    : null;
  const updateGeneration = usePlaygroundStore((s) => s.updateGeneration);
  const setResultAsReference = usePlaygroundStore((s) => s.useResultAsReference);
  const featuredByGen = usePlaygroundStore((s) => s.featuredByGen);
  const toggleFeatured = usePlaygroundStore((s) => s.toggleFeatured);
  const featured = output ? featuredByGen[generation.id] === output.id : false;

  const handleDownload = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!mediaUrl) return;
    try {
      const resp = await apiFetch(mediaUrl);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = output?.media_path?.split('/').pop() || 'download';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      window.open(mediaUrl, '_blank');
    }
  }, [mediaUrl, output]);

  const handleSaveToLibrary = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!output || saving) return;
    setSaving(true);
    try {
      const newSaved = !saved;
      if (newSaved) {
        await playgroundApi.saveToLibrary(generation.id, output.id);
      }
      const updatedOutputs = generation.outputs.map((o) =>
        o.id === output.id ? { ...o, saved_to_library: newSaved } : o
      );
      updateGeneration({ ...generation, outputs: updatedOutputs });
    } catch (err) {
      console.error('[Playground] Save to library failed:', err);
    } finally {
      setSaving(false);
    }
  }, [generation, output, saved, saving, updateGeneration]);

  const handleUseAsReference = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (!output?.media_path) return;
    setResultAsReference(output.media_path, output.media_type);
  }, [output, setResultAsReference]);

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${prompt}，${t('card.expand')}`}
      className={`group rounded-[20px] border bg-glass atelier-asset-card overflow-hidden transition cursor-pointer ${saved ? 'border-primary/40 ring-1 ring-primary/30' : 'border-glass-border hover:border-foreground/30'}`}
      onClick={() => onOpenDetail?.(generation, output.id)}
      onKeyDown={(event) => {
        if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          onOpenDetail?.(generation, output.id);
        }
      }}
    >
      {/* Media area */}
      <div className="relative overflow-hidden bg-elevated" style={{ aspectRatio: '16/9' }}>
        {mediaUrl ? (
          isVideo ? (
            <div className="relative w-full h-full bg-gradient-to-br from-elevated to-surface">
              <video
                src={`${mediaUrl}#t=0.001`}
                poster={thumbnailUrl || undefined}
                aria-label={prompt}
                muted
                playsInline
                preload={thumbnailUrl ? 'metadata' : 'auto'}
                className="w-full h-full object-cover"
              />
              <span className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-black/55 text-foreground/90 shadow-lg backdrop-blur-sm">
                  <Video className="h-5 w-5" />
                </span>
              </span>
            </div>
          ) : (
            <PreviewImage
              src={output.media_path}
              alt={prompt}
              noLightbox
              className="h-full w-full"
              imgClassName="object-cover"
              diagnosticContext="playground-grid-result"
            />
          )
        ) : (
          <div className="w-full h-full bg-gradient-to-br from-elevated to-surface" />
        )}

        {/* Amber halation overlay — only when saved to library */}
        {saved && (
          <div className="atelier-proj-halation pointer-events-none absolute inset-0 z-[1]" />
        )}

        {/* Top-left badges: featured (best-of-batch) + video mode */}
        {(featured || isVideo) && (
          <div className="absolute top-2 left-2 z-[3] flex items-center gap-1.5">
            {featured && (
              <span
                className="inline-flex items-center gap-1 font-mono text-[0.5625rem] uppercase tracking-[0.08em] bg-status-starred-bg text-status-starred-fg border border-status-starred-border rounded px-[6px] py-[2px] backdrop-blur-sm"
                title={t('card.featured')}
              >
                <Crown className="w-2.5 h-2.5 fill-status-starred-solid" />
                {t('card.featured')}
              </span>
            )}
            {isVideo && (
              <span className="font-mono text-[0.5625rem] bg-black/60 text-foreground/80 backdrop-blur-sm rounded px-[6px] py-[2px] uppercase">
                {t(`mode.${mode}`)}
              </span>
            )}
          </div>
        )}

        {/* Saved pill top-right */}
        {saved && (
          <span className="absolute top-2 right-2 z-[2] atelier-badge font-mono text-[0.5625rem] bg-primary/15 text-primary border border-primary/30 rounded px-[6px] py-[2px] uppercase">
            {t('card.saved')}
          </span>
        )}

        {/* Bottom gradient toolbar — appears on hover */}
        <div className="absolute bottom-0 left-0 right-0 z-[2] flex h-14 items-end justify-end gap-1.5 bg-gradient-to-t from-black/75 to-transparent px-3 pb-2 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
          <button
            onClick={handleDownload}
            aria-label={t('card.download')}
            className="flex h-10 w-10 items-center justify-center rounded-full bg-elevated backdrop-blur-sm transition hover:bg-hover-bg"
            title={t('card.download')}
          >
            <Download className="w-3.5 h-3.5 text-foreground" />
          </button>
          <button
            onClick={handleUseAsReference}
            aria-label={t('card.useAsReference')}
            className="flex h-10 w-10 items-center justify-center rounded-full bg-elevated backdrop-blur-sm transition hover:bg-hover-bg"
            title={t('card.useAsReference')}
          >
            <Replace className="w-3.5 h-3.5 text-foreground" />
          </button>
          {output?.media_type === 'image' && onGenerateVideo && (
            <button
              onClick={(e) => { e.stopPropagation(); onGenerateVideo(output.media_path); }}
              aria-label={t('card.generateVideo')}
              className="flex h-10 w-10 items-center justify-center rounded-full bg-elevated backdrop-blur-sm transition hover:bg-hover-bg"
              title={t('card.generateVideo')}
            >
              <Video className="w-3.5 h-3.5 text-foreground" />
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); if (output) toggleFeatured(generation.id, output.id); }}
            aria-label={t('card.featured')}
            aria-pressed={featured}
            className={`flex h-10 w-10 items-center justify-center rounded-full backdrop-blur-sm transition ${featured ? 'bg-status-starred-bg' : 'bg-elevated hover:bg-hover-bg'}`}
            title={t('card.featured')}
          >
            <Crown className={`w-3.5 h-3.5 ${featured ? 'text-status-starred-solid fill-status-starred-solid' : 'text-foreground'}`} />
          </button>
          <button
            onClick={handleSaveToLibrary}
            aria-label={saved ? t('card.saved') : t('card.saveToLibrary')}
            aria-pressed={saved}
            className={`flex h-10 w-10 items-center justify-center rounded-full backdrop-blur-sm transition ${saved ? 'bg-primary/15' : 'bg-elevated hover:bg-hover-bg'}`}
            title={saved ? t('card.saved') : t('card.saveToLibrary')}
          >
            <Bookmark className={`w-3.5 h-3.5 ${saved ? 'text-primary fill-current' : 'text-foreground'}`} />
          </button>
        </div>
      </div>

      {/* Info area */}
      <div className="px-3 py-[10px]">
        <p className="text-[0.6875rem] text-text-secondary line-clamp-2 mb-1.5">{prompt}</p>
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="font-mono text-[0.5625rem] bg-glass text-text-muted rounded px-[6px] py-[2px]">
            {modelDisplayName(model_id)}
          </span>
          {/* Size or resolution tag */}
          {generation.parameters.size && (
            <span className="font-mono text-[0.5625rem] bg-glass text-text-muted rounded px-[6px] py-[2px]">
              {(generation.parameters.size as string).replace('*', '×').replace('x', '×')}
            </span>
          )}
          {generation.parameters.resolution && !generation.parameters.size && (
            <span className="font-mono text-[0.5625rem] bg-glass text-text-muted rounded px-[6px] py-[2px]">
              {generation.parameters.resolution as string}
            </span>
          )}
          {/* Mode badge */}
          <span className="font-mono text-[0.5625rem] bg-primary/10 text-primary/70 rounded px-[6px] py-[2px] uppercase">
            {t(`mode.${mode}`)}
          </span>
          <span className="font-mono text-[0.5625rem] text-text-muted ml-auto">{formatTime(created_at)}</span>
          {saved && (
            <span className="flex items-center gap-0.5 text-[0.5625rem] text-primary">
              <Bookmark className="w-2.5 h-2.5 fill-current" />
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ResultCard({ generation, outputIndex = 0, onGenerateVideo, onRetry, onOpenDetail, onDelete }: ResultCardProps) {
  const { status, prompt, model_id, created_at } = generation;
  const t = useTranslations('playground');
  const modelDisplayName = useModelDisplayName();

  // ─── PROCESSING STATE ───────────────────────────────────────────────────────
  if (status === 'pending' || status === 'processing') {
    return (
      <div className="rounded-[20px] border border-glass-border bg-glass atelier-asset-card overflow-hidden">
        {/* Media area */}
        <div className="relative overflow-hidden bg-elevated" style={{ aspectRatio: '16/9' }}>
          {/* Skeleton shimmer */}
          <div className="absolute inset-0 overflow-hidden">
            <div
              className="absolute inset-0 animate-shimmer"
              style={{
                background:
                  'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.03) 50%, transparent 100%)',
                backgroundSize: '200% 100%',
              }}
            />
          </div>

          {/* Centered spinner + text */}
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
            <div className="w-6 h-6 border-2 border-glass-border border-t-primary rounded-full animate-spin" />
            <span className="font-mono text-[0.625rem] text-text-muted uppercase">
              {status === 'pending' ? t('card.queued') : t('card.processing')}
            </span>
          </div>

          {/* Progress bar */}
          <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-glass">
            <div
              className="h-full bg-primary transition-all duration-1000 ease-out"
              style={{ width: `${getElapsedProgress(created_at)}%` }}
            />
          </div>
        </div>

        {/* Info area */}
        <div className="px-3 py-[10px]">
          <p className="text-[0.6875rem] text-text-secondary line-clamp-2 mb-1.5">{prompt}</p>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[0.5625rem] bg-glass text-text-muted rounded px-[6px] py-[2px]">
              {modelDisplayName(model_id)}
            </span>
            <span className="font-mono text-[0.5625rem] text-text-muted">
              {formatTime(created_at)}
            </span>
          </div>
        </div>
      </div>
    );
  }

  // ─── FAILED STATE ───────────────────────────────────────────────────────────
  if (status === 'failed') {
    return <FailedCard generation={generation} onRetry={onRetry} onDelete={onDelete} />;
  }

  // ─── COMPLETED STATE ────────────────────────────────────────────────────────
  return <CompletedCard generation={generation} outputIndex={outputIndex} onGenerateVideo={onGenerateVideo} onOpenDetail={onOpenDetail} />;
}
