'use client';

import { useTranslations } from 'next-intl';
import { usePlaygroundStore, type PlaygroundMode } from './usePlaygroundStore';

// The New API catalog currently supports image generation/editing and
// Seedance text/image-to-video. Unsupported R2V/V2V modes are not rendered.
const IMAGE_MODES: PlaygroundMode[] = ['t2i', 'i2i'];
const VIDEO_MODES: PlaygroundMode[] = ['t2v', 'i2v'];

export default function ModeSelector() {
  const t = useTranslations('playground');
  const mode = usePlaygroundStore((s) => s.mode);
  const setMode = usePlaygroundStore((s) => s.setMode);

  const renderPill = (key: PlaygroundMode) => {
    const active = mode === key;
    return (
      <button
        key={key}
        type="button"
        aria-pressed={active}
        onClick={() => setMode(key)}
        className={[
          'flex-1 rounded-full px-3 py-1.5 text-[0.6875rem] font-semibold text-center transition-all cursor-pointer',
          active
            ? 'bg-surface text-foreground shadow-[0_2px_8px_rgba(0,0,0,0.4)] atelier-pill-tab-active'
            : 'text-text-muted hover:text-foreground hover:bg-hover-bg',
        ].join(' ')}
      >
        {t(`mode.${key}`)}
      </button>
    );
  };

  return (
    <div className="space-y-3">
      {/* Image group */}
      <div>
        <div className="mb-1.5 flex items-center gap-2">
          <span className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-text-muted">
            {t('mode.groupImage')}
          </span>
          <span className="h-px flex-1 bg-border-subtle atelier-group-line" />
        </div>
        <div role="group" aria-label={t('mode.groupImage')} className="flex gap-[2px] bg-surface-inset rounded-full p-[3px] atelier-pill-tabs">
          {IMAGE_MODES.map(renderPill)}
        </div>
      </div>

      {/* Video group */}
      <div>
        <div className="mb-1.5 flex items-center gap-2">
          <span className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-text-muted">
            {t('mode.groupVideo')}
          </span>
          <span className="h-px flex-1 bg-border-subtle atelier-group-line" />
        </div>
        <div role="group" aria-label={t('mode.groupVideo')} className="flex gap-[2px] bg-surface-inset rounded-full p-[3px] atelier-pill-tabs">
          {VIDEO_MODES.map(renderPill)}
        </div>
      </div>
    </div>
  );
}
